#!/usr/bin/env python3
"""NorthStar V2 convergence supervisor over pinned real Hermes baseline outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
V2 = HERE.parent
BASE = V2.parent
RUNTIME = V2 / "runtime"
STATE = RUNTIME / "state"
RORCH = RUNTIME / "rorch"
QA = RUNTIME / "qa"
EVIDENCE = V2 / "evidence"
TASK = "NORTHSTAR-HERMES-CONVERGENCE-002"
RUN = "RUN-V2-003"
CORR = "northstar-v2-" + hashlib.sha256(TASK.encode()).hexdigest()[:20]

sys.path.insert(0, str(HERE))
from operational_memory import OperationalMemory  # noqa: E402
from UWS.fsm import FSMState, UWSFSM, create_initial_context  # noqa: E402
from UWS.ledger import Ledger  # noqa: E402
from UWS.models import EventType, IndependentReviewEvidence, QAVerdict, QAVerdictType, ReworkResponse, ReworkResponseType, UWSEvent  # noqa: E402

ACTORS = ("MORCH", "RORCH", "QA", "UWS")
BASE_RESULTS = {
    "handoff": BASE / "runtime/actors/morch/morch_handoff/result.json",
    "build": BASE / "runtime/actors/rorch/rorch_build/result.json",
    "qa1": BASE / "runtime/actors/qa/qa_round1/result.json",
    "fix": BASE / "runtime/actors/rorch/rorch_fix/result.json",
    "qa2": BASE / "runtime/actors/qa/qa_round2/result.json",
    "decision": BASE / "runtime/actors/morch/morch_decision/result.json",
    "ack": BASE / "runtime/actors/rorch/rorch_ack/result.json",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name: str) -> dict[str, Any]:
    path = BASE_RESULTS[name]
    data = json.loads(path.read_text(encoding="utf-8"))
    return {**data, "baseline_sha256": digest(path), "model_evidence": "PINNED_REAL_HERMES_OUTPUT_REPLAY"}


def wsl(path: Path) -> str:
    p = path.resolve(); return f"/mnt/{p.drive[0].lower()}/{p.as_posix()[3:]}"


def bwrap(role: str, command: str, *args: str, expected: int = 0, input_path: Path | None = None) -> None:
    cmd = ["wsl.exe", "-d", "kali-linux", "--", "bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
           "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/etc", "/etc",
           "--ro-bind", wsl(HERE), "/code", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
           "--setenv", "HOME", "/tmp/home", "--setenv", "TMPDIR", "/tmp", "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    if input_path is not None:
        cmd += ["--ro-bind", wsl(input_path), "/input.json"]
    if role == "RORCH":
        cmd += ["--bind", wsl(RORCH), "/role/app", "--bind", wsl(EVIDENCE), "/role/evidence"]
    elif role == "QA":
        cmd += ["--ro-bind", wsl(RORCH), "/role/app", "--bind", wsl(QA), "/role/qa"]
    cmd += ["--chdir", "/code", "/usr/bin/python3", "/code/role_worker.py", command, *args]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
    if proc.returncode != expected:
        raise RuntimeError(f"{role} {command} rc={proc.returncode}, expected={expected}: {proc.stderr[-600:]}")


def start_transport_daemon() -> tuple[subprocess.Popen[str], str]:
    socket_dir = f"/tmp/northstar-v4-transport-{hashlib.sha256(str(V2).encode()).hexdigest()[:12]}"
    socket_path = socket_dir + "/transport.sock"
    key_dir = STATE / "identity_v4"
    cmd = ["wsl.exe", "-d", "kali-linux", "--", "/usr/bin/python3", wsl(HERE / "transport_rpc.py"),
           "serve", "--db", wsl(STATE / "transport_v4.db"), "--key-dir", wsl(key_dir), "--socket", socket_path]
    proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        ready = subprocess.run(["wsl.exe", "-d", "kali-linux", "--", "test", "-S", socket_path]).returncode == 0
        if ready and all((key_dir / f"{a.lower()}.key").exists() for a in ACTORS):
            return proc, socket_dir
        if proc.poll() is not None:
            out, err = proc.communicate(); raise RuntimeError(f"transport daemon exited: {out} {err}")
        time.sleep(0.05)
    proc.terminate(); raise RuntimeError("transport daemon did not become ready")


def actor_rpc(actor: str, operation: str, socket_dir: str, *, payload_path: Path | None = None,
              key_actor: str | None = None, expect_ok: bool = True, **fields: str) -> dict[str, Any]:
    key_path = STATE / "identity_v4" / f"{(key_actor or actor).lower()}.key"
    cmd = ["wsl.exe", "-d", "kali-linux", "--", "bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
           "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/etc", "/etc",
           "--ro-bind", wsl(HERE), "/code", "--ro-bind", wsl(key_path), "/identity/key.bin",
           "--bind", socket_dir, "/ipc", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
           "--setenv", "HOME", "/tmp/home", "--setenv", "PYTHONDONTWRITEBYTECODE", "1"]
    if payload_path is not None:
        cmd += ["--ro-bind", wsl(payload_path), "/payload.json"]
    cmd += ["--chdir", "/code", "/usr/bin/python3", "/code/transport_rpc.py", "client",
            "--socket", "/ipc/transport.sock", "--key", "/identity/key.bin", "--actor", actor, operation]
    if payload_path is not None:
        cmd += ["--payload", "/payload.json"]
    for key, value in fields.items():
        cmd += ["--" + key.replace("_", "-"), value]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
    if proc.returncode != 0 and expect_ok:
        raise RuntimeError(f"actor RPC {actor}/{operation} failed rc={proc.returncode}: {proc.stdout} {proc.stderr}")
    response = json.loads(proc.stdout)
    if not expect_ok and response.get("ok"):
        raise RuntimeError(f"negative actor RPC unexpectedly succeeded: {response}")
    return response


def deliver(socket_dir: str, producer: str, recipient: str, kind: str, payload: dict[str, Any], message_id: str) -> dict[str, Any]:
    mail = RUNTIME / "mail"; mail.mkdir(parents=True, exist_ok=True)
    payload_path = mail / f"{message_id}.json"; payload_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    nonce = hashlib.sha256(f"{message_id}|{producer}|{recipient}".encode()).hexdigest()
    sent = actor_rpc(producer, "send", socket_dir, payload_path=payload_path, recipient=recipient,
                     message_type=kind, message_id=message_id, nonce=nonce)
    if sent.get("state") not in {"PENDING", "DUPLICATE"}: raise RuntimeError(sent)
    consumed = actor_rpc(recipient, "consume", socket_dir, message_id=message_id)
    reconciled = actor_rpc("UWS", "reconcile", socket_dir)
    if reconciled.get("stats", {}).get("acked") != 1: raise RuntimeError(reconciled)
    return {"sent": sent, "consumed": consumed, "reconciled": reconciled}


def transition(fsm: UWSFSM, ctx: Any, target: FSMState, payload: dict[str, Any], source: str) -> None:
    fsm.transition(ctx, target, payload, source=source, parent_event_id=ctx.last_event.event_id if ctx.last_event else None)


def context_from_ledger(ledger: Ledger, memory: OperationalMemory) -> Any:
    ctx = create_initial_context(TASK, RUN, 1, CORR, ledger)
    events = ledger.get_events_for_task_run(TASK, RUN)
    recovered = memory.recover(TASK)
    if events:
        ctx.last_event = events[-1]
        state = (recovered["checkpoint"] or {}).get("state", FSMState.TASK_ASSIGNED.value)
        ctx.current_state = FSMState(state)
        ctx.rework_round = sum(1 for e in events if e.event_type == EventType.REWORK_REQUIRED)
    return ctx


def checkpoint(memory: OperationalMemory, state: FSMState, note: str) -> None:
    memory.checkpoint(
        task_id=TASK,
        state=state.value,
        event_id=f"checkpoint:{state.value}:{time.time_ns()}",
        context={"note": note, "run_id": RUN},
    )


def run(stop_after_rejection: bool) -> int:
    for path in (STATE, RORCH / "home", QA / "home", EVIDENCE): path.mkdir(parents=True, exist_ok=True)
    daemon, socket_dir = start_transport_daemon()
    identity_probe = {
        "qa_key_as_morch": actor_rpc("MORCH", "snapshot", socket_dir, key_actor="QA", expect_ok=False),
        "qa_role_privileged_snapshot": actor_rpc("QA", "snapshot", socket_dir, expect_ok=False),
    }
    (EVIDENCE / "identity_boundary_probe.json").write_text(json.dumps(identity_probe, indent=2, sort_keys=True), encoding="utf-8")
    ledger = Ledger(str(STATE / "uws_v4.db"))
    memory = OperationalMemory(STATE / "memory_v4.db")
    fsm = UWSFSM(ledger)
    ctx = context_from_ledger(ledger, memory)
    try:
        if ctx.current_state == FSMState.TASK_ASSIGNED:
            handoff = load("handoff")
            memory.append(task_id=TASK, kind="OBJECTIVE", actor="MORCH_LOCAL_ADAPTER", payload={"northstar": handoff["requirements"], "human_relay_count": 0})
            deliver(socket_dir, "MORCH", "RORCH", "TASK_HANDOFF", handoff, "v4-task-handoff")
            ledger.append_event(UWSEvent.create(EventType.TASK_HANDOFF, TASK, RUN, 1, CORR, "MORCH", handoff))
            transition(fsm, ctx, FSMState.RORCH_EXECUTING, {"task_handoff": handoff}, "UWS")
            checkpoint(memory, ctx.current_state, "RORCH activated from durable inbox")

            build = load("build")
            build_copy = STATE / "build_v3.json"; build_copy.write_text(json.dumps(build, sort_keys=True), encoding="utf-8")
            bwrap("RORCH", "materialize", "/input.json", "/role/app", input_path=build_copy)
            local1 = EVIDENCE / "v2_rorch_test_round1.json"
            bwrap("RORCH", "local-test", "/role/app", "/role/evidence/v2_rorch_test_round1.json")
            memory.append(task_id=TASK, kind="DEV_CONTEXT", actor="RORCH", payload={"delivery_sha256": digest(build_copy), "local_test": digest(local1)})
            transition(fsm, ctx, FSMState.SELF_TEST, {"execution_complete": True}, "RORCH")
            transition(fsm, ctx, FSMState.SUBMITTED_FOR_QA, {"self_test_passed": True, "qa_submission": {"task_id": TASK, "run_id": RUN, "deliverables": ["app.py"], "evidence_manifest": {"sha256": digest(local1)}, "acceptance_criteria": ["AC-001"]}}, "RORCH")
            deliver(socket_dir, "RORCH", "QA", "DELIVERY", build, "v4-delivery-round1")
            transition(fsm, ctx, FSMState.QA_RUNNING, {"qa_submission": {"task_id": TASK, "run_id": RUN, "deliverables": ["app.py"], "evidence_manifest": {}, "acceptance_criteria": ["AC-001"]}}, "QA")
            probe1 = QA / "qa_probe_round1.json"
            bwrap("QA", "qa-probe", "/role/app", "/role/qa/qa_probe_round1.json", expected=1)
            deny = QA / "permission_probe.json"
            bwrap("QA", "deny-probe", "/role/app", "/role/qa", "/role/state", "/role/qa/permission_probe.json")
            qa1 = load("qa1"); qa1["reproduced_evidence_sha256"] = digest(probe1); qa1["permission_probe_sha256"] = digest(deny)
            deliver(socket_dir, "QA", "RORCH", "QA_REJECTED", qa1, "v4-qa-rejected")
            qv = QAVerdict(task_id=TASK, run_id=RUN, attempt_id=1, correlation_id=CORR, parent_qa_submission_id=ctx.last_event.event_id, verdict=QAVerdictType.REJECTED, findings=["QA-FINDING-001"], independent_review_evidence=IndependentReviewEvidence("QA_BWRAP_RO_PROCESS", str(probe1), ["NTV_FUNCTIONAL"], digest(probe1), "FAIL"), qa_acceptance_criteria_met=False, escalation_required=False)
            transition(fsm, ctx, FSMState.QA_REJECTED, {"qa_verdict": qv.to_dict()}, "QA")
            memory.append(task_id=TASK, kind="ERROR", actor="QA", payload={"finding": qa1, "evidence_sha256": digest(probe1)})
            checkpoint(memory, ctx.current_state, "controlled interruption after durable QA rejection")
            if stop_after_rejection:
                print(json.dumps({"status": "CONTROLLED_INTERRUPT", "state": ctx.current_state.value, "exit": 75}))
                return 75

        if ctx.current_state == FSMState.QA_REJECTED:
            rw = ReworkResponse(task_id=TASK, run_id=RUN, attempt_id=1, correlation_id=CORR, parent_qa_finding_id="QA-FINDING-001", response_type=ReworkResponseType.FIXED, evidence={"transport_message": "v4-qa-rejected"}, requires_morch_escalation=False)
            transition(fsm, ctx, FSMState.REWORK_REQUIRED, {"rework_response": rw.to_dict()}, "UWS")
            transition(fsm, ctx, FSMState.RORCH_EXECUTING, {}, "UWS")
            fix = load("fix"); fix_copy = STATE / "fix_v3.json"; fix_copy.write_text(json.dumps(fix, sort_keys=True), encoding="utf-8")
            bwrap("RORCH", "materialize", "/input.json", "/role/app", input_path=fix_copy)
            local2 = EVIDENCE / "v2_rorch_test_round2.json"
            bwrap("RORCH", "local-test", "/role/app", "/role/evidence/v2_rorch_test_round2.json")
            transition(fsm, ctx, FSMState.SELF_TEST, {"execution_complete": True}, "RORCH")
            transition(fsm, ctx, FSMState.SUBMITTED_FOR_QA, {"self_test_passed": True, "qa_submission": {"task_id": TASK, "run_id": RUN, "deliverables": ["app.py"], "evidence_manifest": {"sha256": digest(local2)}, "acceptance_criteria": ["AC-001"]}}, "RORCH")
            deliver(socket_dir, "RORCH", "QA", "REWORK_RESPONSE", fix, "v4-rework-response")
            transition(fsm, ctx, FSMState.QA_RUNNING, {"qa_submission": {"task_id": TASK, "run_id": RUN, "deliverables": ["app.py"], "evidence_manifest": {}, "acceptance_criteria": ["AC-001"]}}, "QA")
            probe2 = QA / "qa_probe_round2.json"
            bwrap("QA", "qa-probe", "/role/app", "/role/qa/qa_probe_round2.json")
            qa2 = load("qa2"); qa2["reproduced_evidence_sha256"] = digest(probe2)
            deliver(socket_dir, "QA", "MORCH", "QA_PASS", qa2, "v4-qa-pass")
            qv2 = QAVerdict(task_id=TASK, run_id=RUN, attempt_id=1, correlation_id=CORR, parent_qa_submission_id=ctx.last_event.event_id, verdict=QAVerdictType.PASS, findings=[], independent_review_evidence=IndependentReviewEvidence("QA_BWRAP_RO_PROCESS", str(probe2), ["NTV_FUNCTIONAL"], digest(probe2), "PASS"), qa_acceptance_criteria_met=True, escalation_required=False)
            transition(fsm, ctx, FSMState.QA_PASS, {"qa_verdict": qv2.to_dict()}, "QA")
            activation = memory.activate_morch_from_qa_pass(task_id=TASK, event_id="v4-qa-pass", event_type="QA_PASS", source="QA", payload=qa2)
            consumed = memory.consume_morch_activation("v4-qa-pass")
            if consumed["authority_component"] != "MORCH_LOCAL_ADAPTER": raise RuntimeError("MORCH activation mismatch")
            transition(fsm, ctx, FSMState.MORCH_REVIEW, {"evidence_manifest_complete": True, "lease_active": True}, "UWS")
            decision = load("decision"); decision["activation_record_sha256"] = activation.chain_sha256; decision["authority_component"] = "MORCH_LOCAL_ADAPTER"
            memory.append(task_id=TASK, kind="DECISION", actor="MORCH_LOCAL_ADAPTER", payload=decision)
            deliver(socket_dir, "MORCH", "RORCH", "ACCEPTED", decision, "v4-morch-accepted")
            transition(fsm, ctx, FSMState.ACCEPTED, {"morch_decision": "ACCEPTED"}, "MORCH")
            ack = load("ack")
            deliver(socket_dir, "RORCH", "MORCH", "RORCH_ACK", ack, "v4-rorch-ack")
            transition(fsm, ctx, FSMState.TERMINAL_COMPLETED, {"terminal_manifest_complete": True}, "RORCH")
            memory.append(task_id=TASK, kind="RESULT", actor="RORCH", payload={"status": "TERMINAL_COMPLETED", "human_relay_count": 0})
            checkpoint(memory, ctx.current_state, "terminal convergence")

        integrity = ledger.verify_integrity(TASK, RUN)
        transport_snapshot = actor_rpc("UWS", "snapshot", socket_dir)
        result = {
            "status": "NORTHSTAR_V2_FUNCTIONAL_PASS",
            "task_id": TASK, "run_id": RUN, "correlation_id": CORR,
            "fsm_state": ctx.current_state.value, "uws_integrity": integrity,
            "transport_counts": transport_snapshot["counts"], "transport_audit_rows": transport_snapshot["audit_rows"],
            "memory_chain_valid": memory.verify_chain(TASK), "memory_recovery": memory.recover(TASK),
            "qa_permission_probe": json.loads((QA / "permission_probe.json").read_text(encoding="utf-8")),
            "human_relay_count": 0,
            "model_execution": "PINNED_REAL_HERMES_BASELINE_REPLAY",
            "multimodel": "UNVERIFIED_EXTERNAL_DEPENDENCY",
            "morch_component": "MORCH_LOCAL_ADAPTER_NOT_CHATGPT",
        }
        out = EVIDENCE / "v4_functional_result.json"; out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps({"status": result["status"], "state": result["fsm_state"], "evidence": str(out)}))
        return 0
    finally:
        memory.close(); ledger.close()
        try:
            actor_rpc("UWS", "shutdown", socket_dir)
        finally:
            daemon.wait(timeout=10)


def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--stop-after-rejection", action="store_true")
    return run(p.parse_args().stop_after_rejection)


if __name__ == "__main__": raise SystemExit(main())

