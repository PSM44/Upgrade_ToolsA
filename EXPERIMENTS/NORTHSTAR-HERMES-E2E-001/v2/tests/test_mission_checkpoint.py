from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mission_checkpoint import (  # noqa: E402
    CheckpointError, file_sha256, load_checkpoint, resume, write_checkpoint,
)


class MissionCheckpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "src").mkdir()
        self.worker = self.root / "src" / "worker.py"
        self.worker.write_text(
            "import pathlib,sys\npathlib.Path(sys.argv[2]).write_text('ran')\n",
            encoding="utf-8",
        )
        self.state_path = self.root / "NORTHSTAR_STATE.json"
        self.state = {
            "schema_version": "upgrade-ta.northstar-state/v1",
            "mission_id": "NORTHSTAR-DELIVERY-20261209",
            "objective_ids": ["P0", "P2"],
            "workspace_root": str(self.root),
            "source_commit": "b225c01cbbfbdeb5d18b15582816fe46310705a5",
            "source_artifacts": {"src/worker.py": file_sha256(self.worker)},
            "completed_requirements": [],
            "remaining_tasks": ["P0"],
            "next_action": {"id": "resume", "command": [
                "{python}", "src/worker.py", "{checkpoint}",
                str(self.root / "marker.txt"),
            ]},
            "running_processes": [],
            "recovery": {"memory_db": "runtime/state/memory_v4.db"},
            "tests": [],
            "external_dependencies": [],
            "security_restrictions": {
                "allowed_write_root": str(self.root),
                "production_uta006_hold_active": True,
                "allow_production_service_restart": False,
            },
            "last_independently_verified": {},
            "revision": 0,
            "updated_at": "",
            "checkpoint_sha256": "",
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, state=None):
        return write_checkpoint(self.state_path, state or self.state)

    def test_atomic_roundtrip_and_revision(self):
        written = self.write()
        loaded = load_checkpoint(self.state_path)
        self.assertEqual(1, loaded["revision"])
        self.assertEqual(written["checkpoint_sha256"], loaded["checkpoint_sha256"])
        self.assertFalse(list(self.root.glob(".NORTHSTAR_STATE.json.*.tmp")))

    def test_tamper_is_rejected(self):
        self.write()
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        value["remaining_tasks"] = []
        self.state_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(CheckpointError, "SHA-256 mismatch"):
            load_checkpoint(self.state_path)

    def test_source_hash_mismatch_is_rejected(self):
        self.write()
        self.worker.write_text("raise SystemExit(7)\n", encoding="utf-8")
        with self.assertRaisesRegex(CheckpointError, "source artifact hash mismatch"):
            load_checkpoint(self.state_path)

    def test_human_hold_cannot_be_relaxed(self):
        state = copy.deepcopy(self.state)
        state["security_restrictions"]["production_uta006_hold_active"] = False
        self.write(state)
        with self.assertRaisesRegex(CheckpointError, "HUMAN_HOLD"):
            load_checkpoint(self.state_path)

    def test_write_root_escape_is_rejected(self):
        state = copy.deepcopy(self.state)
        state["security_restrictions"]["allowed_write_root"] = str(self.root.parent)
        self.write(state)
        with self.assertRaisesRegex(CheckpointError, "allowed_write_root"):
            load_checkpoint(self.state_path)

    def test_command_escape_is_rejected(self):
        outside = self.root.parent / "outside.py"
        outside.write_text("pass\n", encoding="utf-8")
        try:
            state = copy.deepcopy(self.state)
            state["next_action"]["command"][1] = "../outside.py"
            self.write(state)
            with self.assertRaisesRegex(CheckpointError, "escapes authorized root"):
                resume(self.state_path, dry_run=True)
        finally:
            outside.unlink(missing_ok=True)

    def test_shell_command_is_rejected(self):
        state = copy.deepcopy(self.state)
        state["next_action"]["command"] = ["powershell", "-Command", "echo bad"]
        self.write(state)
        with self.assertRaisesRegex(CheckpointError, "pinned Python"):
            resume(self.state_path, dry_run=True)

    def test_active_process_requires_reconciliation(self):
        state = copy.deepcopy(self.state)
        state["running_processes"] = [{"pid": 123, "status": "UNKNOWN"}]
        self.write(state)
        with self.assertRaisesRegex(CheckpointError, "reconcile"):
            resume(self.state_path, dry_run=True)

    def test_dry_run_does_not_execute(self):
        self.write()
        argv = resume(self.state_path, dry_run=True)
        self.assertEqual(sys.executable, argv[0])
        self.assertFalse((self.root / "marker.txt").exists())

    def test_valid_resume_executes_foreground_without_shell(self):
        self.write()
        self.assertEqual(0, resume(self.state_path))
        self.assertEqual("ran", (self.root / "marker.txt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

