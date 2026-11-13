"""Provider-free tests for the external P2 launch boundary."""
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "p2_external", Path(__file__).resolve().parents[1] / "tools/p2_external.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class ExternalRunnerTests(unittest.TestCase):
    def test_no_witness_never_creates_runtime_or_starts_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "NORTHSTAR_P2_NO_WITNESS"
            with patch.object(runner, "STATE_ROOT", Path(tmp)), patch.object(
                    runner.subprocess, "Popen") as popen:
                with self.assertRaises(runner.GateBlocked):
                    runner.execute(runtime, 30)
                self.assertFalse(runtime.exists())
                popen.assert_not_called()

    def test_arbitrary_json_cannot_supply_independent_assurance(self):
        witness = runner.IndependentWitness()
        with self.assertRaises(runner.GateBlocked):
            witness.verify(runner.DEFAULT_RUNTIME, runner.OBJECTIVE, "before_arm")
        with patch("sys.stderr"), self.assertRaises(SystemExit):
            runner.main(["arm", "--attestation", "/tmp/self_authored.json"])

    def test_preflight_and_arm_are_blocked_without_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "NORTHSTAR_P2_PREFLIGHT"
            with patch.object(runner, "STATE_ROOT", Path(tmp)), patch(
                    "builtins.print"), patch.object(runner.subprocess, "run") as run:
                for command in ("preflight", "arm", "execute"):
                    self.assertEqual(runner.main([command, "--runtime", str(runtime)]), 2)
                run.assert_not_called()
                self.assertFalse(runtime.exists())

    def test_existing_runtime_and_symlink_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "NORTHSTAR_P2_OLD"
            existing.mkdir()
            sentinel = existing / "KEEP"
            sentinel.write_text("untouched")
            link = root / "NORTHSTAR_P2_LINK"
            link.symlink_to(existing, target_is_directory=True)
            with patch.object(runner, "STATE_ROOT", root):
                for path in (existing, link, root / "nested/NORTHSTAR_P2_NEW", Path("relative")):
                    with self.assertRaises(ValueError):
                        runner.validate_runtime(path)
                self.assertEqual(sentinel.read_text(), "untouched")
                self.assertEqual(runner.validate_runtime(root / "NORTHSTAR_P2_NEW"),
                                 root / "NORTHSTAR_P2_NEW")

    def test_one_shot_reuses_existing_debian_supervisor(self):
        command = runner.unit_command(runner.DEFAULT_RUNTIME, 900, "northstar-p2-test")
        self.assertEqual(command[:3], ["systemd-run", "--user", "--collect"])
        self.assertNotIn("--on-calendar", command)
        self.assertNotIn("--scope", command)
        self.assertIn("--property=KillMode=control-group", command)
        self.assertIn("--property=RuntimeMaxSec=1020", command)
        supervisor = runner.supervisor_command(runner.DEFAULT_RUNTIME)
        self.assertTrue(supervisor[1].endswith("/src/persistent_control_plane_debian.py"))
        self.assertIn("model-broker", supervisor)
        self.assertIn(str(runner.ROUTES), supervisor)

    def test_stop_affects_only_owned_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owned, old = root / "owned", root / "old"
            owned.mkdir()
            old.mkdir()
            runner.request_stop(owned)
            self.assertTrue((owned / "STOP").exists())
            self.assertFalse((old / "STOP").exists())

    def test_hash_chain_has_monotonic_observation_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.jsonl"
            chain = runner.EvidenceChain(path)
            chain.append("FIRST", {"pid": 10})
            chain.append("SECOND", {"pid": 11})
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            last = "0" * 64
            for row in rows:
                self.assertEqual(row["previous_sha256"], last)
                digest = row.pop("sha256")
                self.assertEqual(digest, hashlib.sha256(runner.canonical(row)).hexdigest())
                last = digest
            self.assertLess(rows[0]["monotonic_ns"], rows[1]["monotonic_ns"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_process_snapshot_redacts_argv_and_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)
            entry = proc / "123"
            entry.mkdir()
            fields = ["S", "12"] + ["0"] * 17 + ["999"]
            (entry / "stat").write_text("123 (node) " + " ".join(fields))
            (entry / "comm").write_text("node")
            (entry / "cmdline").write_bytes(b"node\0/opt/codex/bin.js\0TOP_SECRET\0")
            (entry / "environ").write_bytes(b"API_KEY=TOP_SECRET")
            rows = runner.process_snapshot(proc)
            self.assertEqual(rows, [{"pid": 123, "ppid": 12, "start_ticks": 999,
                                     "category": "FORBIDDEN_ASSISTANT"}])
            self.assertNotIn("TOP_SECRET", json.dumps(rows))
            self.assertNotIn("cmdline", json.dumps(rows))

    def test_descendant_ancestry(self):
        rows = [{"pid": 1, "ppid": 0}, {"pid": 2, "ppid": 1},
                {"pid": 3, "ppid": 2}, {"pid": 4, "ppid": 0}]
        self.assertEqual([r["pid"] for r in runner.descendants(rows, 1)], [1, 2, 3])

    def test_timeout_stops_and_never_collects_before_quiescence(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "NORTHSTAR_P2_TIMEOUT"
            process = Mock()
            process.pid = 1234
            process.returncode = 0
            witness = Mock()
            def stop_without_quiescence(process, path, chain):
                runner.request_stop(path)
                return False
            with patch.object(runner, "STATE_ROOT", Path(tmp)), patch.object(
                    runner, "validate_routes"), patch.object(runner, "service_members",
                    return_value={runner.os.getpid()}), patch.object(runner, "process_snapshot",
                    return_value=[]), patch.object(runner.subprocess, "Popen",
                    return_value=process), patch.object(runner.time, "monotonic",
                    side_effect=[0, 31]), patch.object(runner, "quiesce",
                    side_effect=stop_without_quiescence), patch.object(
                    runner, "read_control") as read_control:
                result = runner.execute(runtime, 30, witness)
                read_control.assert_not_called()
            self.assertTrue((runtime / "STOP").exists())
            self.assertEqual(result["status"], "P2_FAIL")
            self.assertEqual(result["reason"], "QUIESCENCE_UNPROVEN")
            self.assertFalse(result["quiescent"])

    def test_start_failure_writes_stop_and_fail_without_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "NORTHSTAR_P2_TEST"
            witness = Mock()
            with patch.object(runner, "STATE_ROOT", Path(tmp)), patch.object(
                    runner, "validate_routes"), patch.object(runner, "service_members", return_value={runner.os.getpid()}), patch.object(runner, "process_snapshot", return_value=[]), patch.object(
                    runner.subprocess, "Popen", side_effect=OSError("PRIVATE_SECRET")):
                result = runner.execute(runtime, 30, witness)
            self.assertEqual(result["status"], "P2_FAIL")
            self.assertTrue((runtime / "STOP").exists())
            self.assertFalse(result["automated_pass"])
            self.assertNotIn("PRIVATE_SECRET", (runtime / "observer.jsonl").read_text())


if __name__ == "__main__":
    unittest.main()
