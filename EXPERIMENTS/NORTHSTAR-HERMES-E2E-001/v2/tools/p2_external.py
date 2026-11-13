#!/usr/bin/env python3
"""P2 external entrypoint; no installed/approved host witness means fail closed.

A future reviewed adapter must authenticate a continuous independent Windows host
observer and account evidence. JSON supplied by this program or the caller is not
independent assurance. The current adapter intentionally cannot authorize a run.
Linux /proc samples are supplementary, never proof about Windows processes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

V2 = Path(__file__).resolve().parents[1]
STATE_ROOT = Path("/home/psm/northstar/state")
DEFAULT_RUNTIME = STATE_ROOT / "NORTHSTAR_P2_CODEX_CLOSED_20261111_001"
OBJECTIVE = "NORTHSTAR-P2-RETRY-SCHEDULE-20261111-001"
ROUTES = V2 / "config/model_routes_p2.json"
WITNESS_REQUIREMENTS = {
    "host": [
        "independent authenticated monitor identity and preapproved trust anchor",
        "Windows host process enumeration with zero Codex and Claude processes",
        "objective and exact runtime binding",
        "continuous coverage before supervisor start through complete quiescence",
        "trusted UTC timestamps, monotonic sequence, heartbeat and signed hash chain",
        "no gaps, replay, stale reports or unauthenticated self-authored JSON",
    ],
    "account": [
        "authoritative provider account identity and Extra Usage OFF",
        "known remaining included allowance sufficient for six model invocations; each invocation can make multiple provider requests",
        "fresh authenticated evidence bound to this run; route flags are not proof",
    ],
}
BLOCKERS = [
    "HOST_OBSERVER_NOT_CONFIGURED",
    "EXTRA_USAGE_OFF_NOT_INDEPENDENTLY_VERIFIED",
    "REMAINING_ALLOWANCE_UNKNOWN",
]


class GateBlocked(RuntimeError):
    pass


class IndependentWitness:
    """No runtime plugin or caller-chosen verifier is trusted by this entrypoint.

    Implementing a real adapter/trust anchor requires separate review. Both
    startup and every heartbeat must verify host AND account coverage.
    """

    def verify(self, runtime: Path, objective: str, phase: str) -> dict:
        raise GateBlocked(";".join(BLOCKERS))


def verify_witness(witness, runtime: Path, objective: str, phase: str):
    """Testable boundary; no dictionary verdict has operational accreditation.

    The existing injected-object seam supports mocked lifecycle unit tests only.
    No CLI plugin/configuration admits an injected witness. A real typed verifier
    and trust-anchor admission require separate implementation and review.
    """
    result = witness.verify(runtime, objective, phase)
    if isinstance(result, dict):
        raise GateBlocked("NO_ACCREDITED_VERDICT_CONSUMER;SIMULATION_NOT_ACCREDITED")
    return result


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def validate_runtime(runtime: Path) -> Path:
    if not runtime.is_absolute():
        raise ValueError("runtime must be absolute")
    resolved = runtime.resolve()
    if runtime != resolved or resolved.parent != STATE_ROOT:
        raise ValueError("runtime must be a direct, nonsymlink child of the isolated state root")
    if not re.fullmatch(r"NORTHSTAR_P2_[A-Z0-9_]+", resolved.name):
        raise ValueError("runtime name must use NORTHSTAR_P2_ and uppercase safe characters")
    if resolved.exists() or resolved.is_symlink():
        raise ValueError("existing runtime is preserved; choose a fresh runtime")
    return resolved


def validate_routes(path: Path = ROUTES) -> None:
    config = json.loads(path.read_text())
    limits = config["limits"]
    if limits["max_calls"] != 6 or limits["extra_usage"] is not False:
        raise ValueError("P2 route limits must explicitly cap calls at six and disable extra usage")
    expected = {"RORCH": "opencode-go/glm-5.3-flash",
                "QA": "opencode-go/mimo-v2.6-flash",
                "MORCH": "opencode-go/glm-5.3-flash"}
    if set(config["roles"]) != set(expected):
        raise ValueError("unexpected model role set")
    for actor, model in expected.items():
        route = config["roles"][actor]
        if route["model"] != model or route["provider"] != "opencode-go":
            raise ValueError("unexpected paid or fallback route")


def supervisor_command(runtime: Path) -> list[str]:
    return ["/usr/bin/python3", str(V2 / "src/persistent_control_plane_debian.py"),
            "run", "--backend", "model-broker", "--model-routes", str(ROUTES),
            "--runtime", str(runtime)]


def unit_command(runtime: Path, timeout: float, unit: str) -> list[str]:
    return ["systemd-run", "--user", "--collect", "--unit", unit,
            "--property=Type=exec", "--property=KillMode=control-group",
            "--property=UMask=0077", "--property=RuntimeMaxSec=" + str(int(timeout + 120)),
            "--setenv=PYTHONDONTWRITEBYTECODE=1",
            "/usr/bin/python3", str(Path(__file__).resolve()), "execute",
            "--runtime", str(runtime), "--timeout", str(timeout)]


def process_snapshot(proc_root: Path = Path("/proc")) -> list[dict]:
    """Only PID, PPID, start ticks and category leave /proc; no argv/env."""
    rows = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            fields = stat[stat.rfind(")") + 2:].split()
            comm = (entry / "comm").read_text().strip().lower()
            # Read argv solely for process identity; never persist raw arguments.
            argv = (entry / "cmdline").read_bytes().split(b"\0")
            executable = os.path.basename(argv[0].decode(errors="replace")).lower() if argv else ""
            forbidden = any(token in comm or token in executable for token in ("codex", "claude"))
            # Node-hosted CLI identity may appear as argv[1], but shell text is
            # deliberately not interpreted as an executable.
            if executable in {"node", "nodejs", "bun"} and len(argv) > 1:
                script = argv[1].decode(errors="replace").lower()
                forbidden |= any(token in script for token in ("codex", "claude"))
            rows.append({"pid": int(entry.name), "ppid": int(fields[1]),
                         "start_ticks": int(fields[19]),
                         "category": "FORBIDDEN_ASSISTANT" if forbidden else "OTHER"})
        except FileNotFoundError:
            continue  # process exited during enumeration
        except (PermissionError, IndexError, ValueError) as exc:
            raise GateBlocked("LINUX_PROCESS_ENUMERATION_INCOMPLETE") from exc
    return sorted(rows, key=lambda row: row["pid"])


def descendants(rows: list[dict], root_pid: int) -> list[dict]:
    selected = {root_pid}
    while True:
        added = {row["pid"] for row in rows if row["ppid"] in selected}
        if added <= selected:
            return [row for row in rows if row["pid"] in selected]
        selected |= added


class EvidenceChain:
    def __init__(self, path: Path):
        self.path = path
        self.previous = "0" * 64
        self.sequence = 0

    def append(self, event: str, data: dict) -> None:
        self.sequence += 1
        body = {"sequence": self.sequence, "monotonic_ns": time.monotonic_ns(),
                "utc_ns": time.time_ns(), "event": event, "data": data,
                "previous_sha256": self.previous}
        self.previous = hashlib.sha256(canonical(body)).hexdigest()
        body["sha256"] = self.previous
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(canonical(body).decode() + "\n")
        self.path.chmod(0o600)


def read_control(runtime: Path, objective: str) -> tuple[str | None, list[dict]]:
    db = runtime / "control_plane.db"
    if not db.exists():
        return None, []
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=1) as connection:
        state_row = connection.execute(
            "SELECT state FROM objectives WHERE objective_id=?", (objective,)).fetchone()
        events = [{"source_seq": row[0], "source_utc": row[1], "event_type": row[2]}
                  for row in connection.execute(
                      "SELECT seq,timestamp,event_type FROM events ORDER BY seq")]
    return state_row[0] if state_row else None, events


def request_stop(runtime: Path) -> None:
    # Called only for a runtime exclusively created by this invocation.
    (runtime / "STOP").write_text("P2 external runner stop\n", encoding="utf-8")


def service_members() -> set[int]:
    """Read the current dedicated systemd service cgroup, including child scopes."""
    lines = Path("/proc/self/cgroup").read_text().splitlines()
    relative = next((line[3:] for line in lines if line.startswith("0::")), None)
    if relative is None or not re.search(r"/northstar-p2-[a-f0-9]+\.service$", relative):
        raise GateBlocked("DEDICATED_SYSTEMD_SERVICE_CGROUP_REQUIRED")
    root = (Path("/sys/fs/cgroup") / relative.lstrip("/")).resolve()
    if not root.is_relative_to(Path("/sys/fs/cgroup")):
        raise GateBlocked("INVALID_SERVICE_CGROUP")
    members = set()
    for path in root.rglob("cgroup.procs"):
        members.update(int(value) for value in path.read_text().split())
    return members


def quiesce(process: subprocess.Popen, runtime: Path, chain: EvidenceChain) -> bool:
    request_stop(runtime)
    chain.append("STOP_REQUESTED", {})
    try:
        process.wait(timeout=35)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        chain.append("SUPERVISOR_GROUP_TERMINATED", {})
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
            chain.append("SUPERVISOR_GROUP_KILLED", {})
    # A group can outlive its leader; do not collect while it still exists.
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return service_members() <= {os.getpid()}
    os.killpg(process.pid, signal.SIGTERM)
    for _ in range(40):
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return service_members() <= {os.getpid()}
        time.sleep(0.25)
    return False


def execute(runtime: Path, timeout: float, witness: IndependentWitness | None = None) -> dict:
    witness = witness or IndependentWitness()
    runtime = validate_runtime(runtime)
    verify_witness(witness, runtime, OBJECTIVE, "before_create")
    validate_routes()
    if service_members() != {os.getpid()}:
        raise GateBlocked("SERVICE_CGROUP_NOT_EXCLUSIVE")
    first_rows = process_snapshot()
    if any(row["category"] == "FORBIDDEN_ASSISTANT" for row in first_rows):
        raise GateBlocked("LINUX_FORBIDDEN_ASSISTANT_PRESENT")
    os.umask(0o077)
    runtime.mkdir(mode=0o700)  # exclusive: no reuse or broad cleanup
    private = runtime / "private"
    private.mkdir(mode=0o700)
    chain = EvidenceChain(runtime / "observer.jsonl")
    chain.append("RUN_START", {"objective_id": OBJECTIVE, "linux_processes": first_rows})
    process = None
    status = "P2_FAIL"
    reason = "RUNNER_ERROR"
    complete = False
    quiescent = False
    started = time.monotonic()
    nonce = uuid.uuid4().hex
    try:
        with (private / "supervisor.log").open("wb") as log:
            process = subprocess.Popen(supervisor_command(runtime), cwd=V2,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True,
                                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            chain.append("SUPERVISOR_STARTED", {"pid": process.pid})
            submitted = False
            last_seq = 0
            while time.monotonic() - started < timeout:
                verify_witness(witness, runtime, OBJECTIVE, "heartbeat")
                rows = process_snapshot()
                chain.append("LINUX_SAMPLE", {"processes": rows,
                             "supervisor_descendants": descendants(rows, process.pid)})
                if any(row["category"] == "FORBIDDEN_ASSISTANT" for row in rows):
                    raise GateBlocked("LINUX_FORBIDDEN_ASSISTANT_PRESENT")
                if process.poll() is not None:
                    reason = "SUPERVISOR_EXITED"
                    break
                endpoint_path = runtime / "endpoint.json"
                if not submitted and endpoint_path.exists():
                    endpoint = json.loads(endpoint_path.read_text())
                    if endpoint.get("delegate_socket") and (runtime / "identity/human.key").exists():
                        cmd = ["/usr/bin/python3", str(V2 / "src/morch_delegate.py"),
                               "client", "--socket", endpoint["delegate_socket"],
                               "--key", str(runtime / "identity/human.key"),
                               "--operation", "submit", "--objective-id", OBJECTIVE,
                               "--fixture-spec", "retry_schedule_v1",
                               "--objective", "Implement and independently verify retry_schedule_v1.",
                               "--nonce", nonce]
                        with (private / "submit.log").open("wb") as submit_log:
                            result = subprocess.run(cmd, cwd=V2, stdout=submit_log,
                                                    stderr=subprocess.STDOUT, timeout=15)
                        if result.returncode:
                            raise RuntimeError("DELEGATE_SUBMIT_FAILED")
                        submitted = True
                        chain.append("OBJECTIVE_SUBMITTED", {"objective_id": OBJECTIVE, "nonce": nonce})
                state, events = read_control(runtime, OBJECTIVE)
                for event in events:
                    if event["source_seq"] > last_seq:
                        chain.append("CONTROL_EVENT_OBSERVED", event)
                        last_seq = event["source_seq"]
                if state == "COMPLETED":
                    complete = True
                    reason = "INDEPENDENT_REVIEW_REQUIRED"
                    break
                if state in {"FAILED", "ABORTED"}:
                    reason = "OBJECTIVE_FAILED"
                    break
                time.sleep(0.5)
            else:
                reason = "RUN_TIMEOUT"
    except Exception as exc:
        # Exception strings/provider logs can contain credentials; only type exported.
        reason = "GATE_REVOKED" if isinstance(exc, GateBlocked) else type(exc).__name__
        chain.append("RUN_EXCEPTION", {"type": type(exc).__name__})
    finally:
        if process is not None:
            try:
                quiescent = quiesce(process, runtime, chain)
            except Exception as exc:
                chain.append("QUIESCENCE_ERROR", {"type": type(exc).__name__})
                quiescent = False
        else:
            request_stop(runtime)
            quiescent = True
        if quiescent:
            try:
                verify_witness(witness, runtime, OBJECTIVE, "after_quiescence")
                state, events = read_control(runtime, OBJECTIVE)
                chain.append("QUIESCENT_CONTROL_SUMMARY", {"state": state, "event_count": len(events)})
                if complete and process is not None and process.returncode == 0:
                    status = "P2_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW"
            except Exception:
                reason = "FINAL_WITNESS_OR_COLLECTION_FAILED"
        else:
            reason = "QUIESCENCE_UNPROVEN"
        report = {"status": status, "reason": reason, "objective_id": OBJECTIVE,
                  "runtime": str(runtime), "quiescent": quiescent,
                  "ntv_functional": "UNREVIEWED", "ntv_root": "UNREVIEWED",
                  "raw_logs": "PRIVATE_NOT_PUBLISHABLE", "automated_pass": False}
        chain.append("RUN_END", report)
        report["observer_chain_head"] = chain.previous
        (runtime / "runner_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "arm", "execute"))
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args(argv)
    try:
        runtime = validate_runtime(args.runtime)
        if not 30 <= args.timeout <= 1800:
            raise ValueError("timeout must be between 30 and 1800 seconds")
        if args.command == "preflight":
            print(json.dumps({"status": "P2_PARTIAL", "execution_authorized": False,
                              "blockers": BLOCKERS + ([] if (Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "bus").is_socket()
                                                     else ["USER_SYSTEMD_BUS_UNAVAILABLE"]),
                              "witness_requirements": WITNESS_REQUIREMENTS,
                              "supervisor_command": supervisor_command(runtime),
                              "prepared_one_shot_command": unit_command(
                                  runtime, args.timeout, "northstar-p2-" + uuid.uuid4().hex),
                              "runtime_created": False}, indent=2))
            return 2
        IndependentWitness().verify(runtime, OBJECTIVE, "before_arm")
        if args.command == "arm":
            validate_routes()
            unit = "northstar-p2-" + uuid.uuid4().hex
            subprocess.run(unit_command(runtime, args.timeout, unit), check=True,
                           capture_output=True, timeout=20)
            print(json.dumps({"status": "ARMED_NOT_ACCEPTED", "unit": unit}))
            return 0
        result = execute(runtime, args.timeout)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "P2_CANDIDATE_REQUIRES_INDEPENDENT_REVIEW" else 3
    except (GateBlocked, ValueError) as exc:
        print(json.dumps({"status": "P2_PARTIAL", "execution_authorized": False,
                          "blocker": str(exc), "runtime_created": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
