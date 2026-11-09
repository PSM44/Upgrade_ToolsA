#!/usr/bin/env python3
"""Independent adversarial verifier for NorthStar control-plane primitives.

This file is intentionally outside ``src`` and never mutates implementation
state.  It builds disposable databases and mount fixtures, then emits one JSON
verdict.  A later dispatcher-level verifier can compose these checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
V2 = HERE.parent
SRC = V2 / "src"
if Path("/code/operational_memory.py").is_file():
    SRC = Path("/code")
sys.path.insert(0, str(SRC))

from operational_memory import IntegrityError, OperationalMemory  # noqa: E402
from role_worker import materialize  # noqa: E402
from secure_transport import (  # noqa: E402
    AuthenticationError,
    AuthorizationError,
    LeaseError,
    ReplayError,
    SecureTransport,
)


def result(name: str, passed: bool, detail: str, **extra: object) -> dict[str, object]:
    return {"name": name, "passed": passed, "detail": detail, **extra}


def wsl(path: Path) -> str:
    resolved = path.resolve()
    return f"/mnt/{resolved.drive[0].lower()}/{resolved.as_posix()[3:]}"


def actor_keys() -> dict[str, bytes]:
    return {actor: hashlib.sha256(f"ntv:{actor}".encode()).digest() for actor in ("MORCH", "RORCH", "QA", "UWS")}


def new_transport(path: Path) -> tuple[SecureTransport, dict[str, bytes]]:
    bootstrap = b"independent-ntv-bootstrap"
    keys = actor_keys()
    transport = SecureTransport(path, bootstrap, replay_window_s=300)
    for actor, key in keys.items():
        transport.register_actor(actor, actor, key, bootstrap_secret=bootstrap)
    return transport, keys


def check_transport(tmp: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    transport, keys = new_transport(tmp / "transport.db")

    try:
        transport.enqueue(producer="MORCH", producer_key=keys["QA"], recipient="RORCH", message_type="TASK_HANDOFF", payload={}, message_id="spoof", nonce="spoof-nonce")
        checks.append(result("fake_sender_identity", False, "wrong capability was accepted"))
    except AuthenticationError:
        checks.append(result("fake_sender_identity", True, "wrong capability rejected"))

    first = transport.enqueue(producer="MORCH", producer_key=keys["MORCH"], recipient="RORCH", message_type="TASK_HANDOFF", payload={"objective": "x"}, message_id="m1", nonce="nonce-m1")
    duplicate = transport.enqueue(producer="MORCH", producer_key=keys["MORCH"], recipient="RORCH", message_type="TASK_HANDOFF", payload={"objective": "x"}, message_id="m1", nonce="nonce-m1")
    checks.append(result("duplicate_delivery", first == "PENDING" and duplicate == "DUPLICATE", f"first={first}, duplicate={duplicate}"))

    try:
        transport.enqueue(producer="MORCH", producer_key=keys["MORCH"], recipient="RORCH", message_type="TASK_HANDOFF", payload={"objective": "y"}, message_id="m2", nonce="nonce-m1")
        checks.append(result("replay_nonce", False, "nonce replay accepted"))
    except ReplayError:
        checks.append(result("replay_nonce", True, "nonce replay rejected"))

    delivery = transport.claim(consumer="RORCH", consumer_key=keys["RORCH"], lease_s=30)
    assert delivery is not None and delivery.message_id == "m1"
    try:
        transport.ack(consumer="RORCH", consumer_key=keys["RORCH"], message_id="m1", claim_token="wrong-token")
        checks.append(result("failed_ack", False, "wrong claim token accepted"))
    except LeaseError:
        checks.append(result("failed_ack", True, "wrong claim token rejected"))
    transport.ack(consumer="RORCH", consumer_key=keys["RORCH"], message_id="m1", claim_token=delivery.claim_token)

    transport.enqueue(producer="MORCH", producer_key=keys["MORCH"], recipient="RORCH", message_type="TASK_HANDOFF", payload={"objective": "lease"}, message_id="lease", nonce="nonce-lease")
    leased = transport.claim(consumer="RORCH", consumer_key=keys["RORCH"], lease_s=0.01)
    assert leased is not None and leased.message_id == "lease"
    stats = transport.reconcile(now=time.time() + 1)
    reclaimed = transport.claim(consumer="RORCH", consumer_key=keys["RORCH"], lease_s=30)
    lease_ok = reclaimed is not None and reclaimed.message_id == "lease" and reclaimed.claim_token != leased.claim_token
    checks.append(result("expired_lease", lease_ok and stats.get("requeued", 0) == 1, f"stats={stats}, reclaimed={lease_ok}"))

    try:
        transport.enqueue(producer="RORCH", producer_key=keys["RORCH"], recipient="MORCH", message_type="QA_PASS", payload={"verdict": "PASS"}, message_id="forged-pass", nonce="nonce-forged")
        checks.append(result("false_qa_acceptance", False, "RORCH forged QA_PASS"))
    except AuthorizationError:
        checks.append(result("false_qa_acceptance", True, "RORCH QA_PASS rejected by route authorization"))
    return checks


def check_memory(tmp: Path) -> list[dict[str, object]]:
    db = tmp / "memory.db"
    task = "NTV-CONTROL-PLANE"
    with OperationalMemory(db) as memory:
        memory.append(task_id=task, kind="OBJECTIVE", actor="MORCH_LOCAL_ADAPTER", record_id="objective-1", payload={"objective": "persist"})
        memory.checkpoint(task_id=task, event_id="checkpoint-1", state="TASK_ASSIGNED", context={"cursor": 1})
    with OperationalMemory(db) as reopened:
        recovered = reopened.recover(task)
        restart_ok = reopened.verify_chain(task) and recovered["checkpoint"]["state"] == "TASK_ASSIGNED"
    checks = [result("memory_database_restart", restart_ok, json.dumps(recovered, sort_keys=True))]

    corrupt = tmp / "memory-corrupt.db"
    corrupt.write_bytes(db.read_bytes())
    with closing(sqlite3.connect(corrupt)) as conn:
        conn.execute("UPDATE operational_memory SET chain_sha256='00' WHERE seq=(SELECT MAX(seq) FROM operational_memory)")
        conn.commit()
    with OperationalMemory(corrupt) as damaged:
        try:
            damaged.verify_chain(task)
            detected = False
        except IntegrityError:
            detected = True
        checks.append(result("memory_recovery_failure", detected, "corrupted chain detected"))
    return checks


def check_patch_validation(tmp: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    app = tmp / "app"
    for name, files in (("empty", {}), ("escape", {"app.py": "", "../escape.py": "", "test_app.py": ""})):
        source = tmp / f"{name}.json"
        source.write_text(json.dumps({"files": files}), encoding="utf-8")
        try:
            materialize(source, app)
            checks.append(result(f"invalid_patch_{name}", False, "invalid file set accepted"))
        except ValueError:
            checks.append(result(f"invalid_patch_{name}", True, "invalid file set rejected"))
    valid = tmp / "valid.json"
    valid.write_text(json.dumps({"files": {"app.py": "x=1\n", "test_app.py": "# test\n"}}), encoding="utf-8")
    materialize(valid, app)
    checks.append(result("valid_patch_control", (app / "app.py").exists() and not (tmp / "escape.py").exists(), "valid exact file set materialized"))
    return checks


def guest_probe(output: Path) -> int:
    observations: dict[str, object] = {}
    for name, path in {
        "app_write": Path("/role/app/forbidden.txt"),
        "code_write": Path("/code/forbidden.txt"),
        "qa_write": Path("/role/qa/allowed.txt"),
        "host_write": Path("/mnt/c/UTA_ISOLATION/ntv-forbidden.txt"),
    }.items():
        try:
            path.write_text(name, encoding="utf-8")
            observations[name] = "SUCCEEDED"
        except Exception as exc:
            observations[name] = f"DENIED:{type(exc).__name__}"
    observations["state_visible"] = Path("/role/state").exists()
    observations["host_visible"] = Path("/mnt/c").exists()
    observations["passed"] = (
        str(observations["app_write"]).startswith("DENIED:")
        and str(observations["code_write"]).startswith("DENIED:")
        and observations["qa_write"] == "SUCCEEDED"
        and str(observations["host_write"]).startswith("DENIED:")
        and not observations["state_visible"]
        and not observations["host_visible"]
    )
    output.write_text(json.dumps(observations, indent=2, sort_keys=True), encoding="utf-8")
    return 0 if observations["passed"] else 1


def check_filesystem(tmp: Path) -> list[dict[str, object]]:
    app = tmp / "fs-app"; qa = tmp / "fs-qa"
    app.mkdir(); qa.mkdir()
    (app / "app.py").write_text("x=1\n", encoding="utf-8")
    output = qa / "mount-probe.json"
    cmd = [
        "wsl.exe", "-d", "kali-linux", "--", "bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/etc", "/etc",
        "--ro-bind", wsl(SRC), "/code", "--ro-bind", wsl(HERE), "/review", "--ro-bind", wsl(app), "/role/app", "--bind", wsl(qa), "/role/qa",
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--setenv", "HOME", "/tmp/home", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "/usr/bin/python3", "/review/ntv_controlplane.py", "--guest-probe", "/role/qa/mount-probe.json",
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
    data = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {"passed": False, "stderr": proc.stderr}
    return [result("qa_filesystem_escape", proc.returncode == 0 and bool(data.get("passed")), json.dumps(data, sort_keys=True), exit_code=proc.returncode)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guest-probe", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.guest_probe:
        return guest_probe(args.guest_probe)
    if not args.output:
        parser.error("--output is required")
    with tempfile.TemporaryDirectory(prefix="ntv-controlplane-", dir=V2 / "runtime") as td:
        tmp = Path(td)
        checks = check_transport(tmp) + check_memory(tmp) + check_patch_validation(tmp) + check_filesystem(tmp)
    payload = {
        "schema": "upgrade-ta.northstar.control-plane-independent-ntv/v1",
        "reviewer": "/root/ntv_controlplane",
        "implementation_modified_by_reviewer": False,
        "checks": checks,
        "pass_count": sum(1 for c in checks if c["passed"]),
        "check_count": len(checks),
    }
    payload["status"] = "PASS" if payload["pass_count"] == payload["check_count"] else "FAIL"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "passed": f"{payload['pass_count']}/{payload['check_count']}", "output": str(args.output)}))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

