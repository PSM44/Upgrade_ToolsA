#!/usr/bin/env python3
"""NorthStar E2E supervisor using Hermes calls plus the existing UWS ledger/FSM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURE = HERE.parent
RUNTIME = FIXTURE / "runtime"
EVIDENCE = FIXTURE / "evidence"
DB = RUNTIME / "northstar.db"
TASK_ID = "NORTHSTAR-HERMES-E2E-001"
RUN_ID = "RUN-001"
CORRELATION_ID = "northstar-" + hashlib.sha256(TASK_ID.encode()).hexdigest()[:24]
HERMES = "/home/aazcl/.local/bin/hermes"
DEFAULT_MODEL = "20261030_RORCH"

sys.path.insert(0, str(HERE))
from UWS.fsm import FSMState, UWSFSM, create_initial_context  # noqa: E402
from UWS.ledger import Ledger  # noqa: E402
from UWS.models import (  # noqa: E402
    AcceptanceCriterion,
    Deliverable,
    EventType,
    EvidenceManifest,
    IndependentReviewEvidence,
    QASubmission,
    QAVerdict,
    QAVerdictType,
    ReworkResponse,
    ReworkResponseType,
    UWSEvent,
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def init_store(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS stage_runs(
          stage TEXT PRIMARY KEY, actor TEXT NOT NULL, status TEXT NOT NULL,
          transport_identity TEXT NOT NULL, input_sha256 TEXT NOT NULL,
          output_path TEXT, output_sha256 TEXT, started_at TEXT NOT NULL,
          completed_at TEXT, process_exit INTEGER, model TEXT, provider TEXT,
          session_id TEXT, usage_path TEXT
        );
        CREATE TABLE IF NOT EXISTS messages(
          message_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
          direction TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL,
          message_type TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
          payload_path TEXT NOT NULL, status TEXT NOT NULL, acked_at TEXT,
          transport_identity TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS supervisor_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO supervisor_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM supervisor_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def json_from_output(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Hermes response did not contain a JSON object")


def wsl_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    rest = resolved.as_posix()[3:]
    return f"/mnt/{drive}/{rest}"


def call_hermes(stage: str, actor: str, prompt: str, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    stage_dir = RUNTIME / "actors" / actor.lower() / stage.lower()
    stage_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = stage_dir / "prompt.txt"
    stdout_path = stage_dir / "stdout.log"
    stderr_path = stage_dir / "stderr.log"
    usage_path = stage_dir / "usage.json"
    result_path = stage_dir / "result.json"
    prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
    input_digest = sha256(prompt_path)

    with sqlite3.connect(DB) as conn:
        init_store(conn)
        existing = conn.execute("SELECT status, output_path FROM stage_runs WHERE stage=?", (stage,)).fetchone()
        if existing and existing[0] == "COMPLETED":
            return json.loads(Path(existing[1]).read_text(encoding="utf-8"))
        conn.execute(
            "INSERT OR REPLACE INTO stage_runs(stage,actor,status,transport_identity,input_sha256,started_at,model,provider,usage_path) VALUES(?,?,?,?,?,?,?,?,?)",
            (stage, actor, "RUNNING", f"supervisor-spawn:{actor}", input_digest, utcnow(), model, "custom", str(usage_path)),
        )
        conn.commit()

    args = [
        "wsl.exe", "-d", "kali-linux", "--", HERMES,
        "--ignore-rules", "--in", wsl_path(stage_dir),
        "--pass-session-id", "--usage-file", wsl_path(usage_path),
        "--provider", "custom", "--model", model, "-z", prompt,
    ]
    proc = subprocess.run(args, text=True, capture_output=True, timeout=300)
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        with sqlite3.connect(DB) as conn:
            conn.execute("UPDATE stage_runs SET status='FAILED', completed_at=?, process_exit=? WHERE stage=?", (utcnow(), proc.returncode, stage))
            conn.commit()
        raise RuntimeError(f"Hermes stage {stage} failed rc={proc.returncode}: {proc.stderr[-500:]}")
    result = json_from_output(proc.stdout)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    usage: dict[str, Any] = {}
    if usage_path.exists():
        usage = json.loads(usage_path.read_text(encoding="utf-8"))
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "UPDATE stage_runs SET status='COMPLETED', output_path=?, output_sha256=?, completed_at=?, process_exit=0, session_id=? WHERE stage=?",
            (str(result_path), sha256(result_path), utcnow(), usage.get("session_id"), stage),
        )
        conn.commit()
    return result


def bwrap(*guest_args: str, expect: int | None = 0) -> subprocess.CompletedProcess[str]:
    fixture = wsl_path(FIXTURE)
    runtime = wsl_path(RUNTIME)
    evidence = wsl_path(EVIDENCE)
    cmd = [
        "wsl.exe", "-d", "kali-linux", "--", "bwrap",
        "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/etc", "/etc", "--ro-bind", fixture, "/workspace",
        "--bind", runtime, "/workspace/runtime", "--proc", "/proc", "--dev", "/dev",
        "--bind", evidence, "/workspace/evidence",
        "--tmpfs", "/tmp", "--setenv", "HOME", "/workspace/runtime/home",
        "--setenv", "TMPDIR", "/tmp", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--chdir", "/workspace", "python3", "/workspace/src/guest_worker.py", *guest_args,
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
    if expect is not None and proc.returncode != expect:
        raise RuntimeError(f"guest worker rc={proc.returncode}, expected={expect}: {proc.stderr[-500:]}")
    return proc


def send_message(conn: sqlite3.Connection, sender: str, recipient: str, kind: str, payload_path: Path, transport_actor: str) -> str:
    digest = sha256(payload_path)
    idem = hashlib.sha256(f"{TASK_ID}|{RUN_ID}|{sender}|{recipient}|{kind}|{digest}".encode()).hexdigest()
    message_id = str(uuid.uuid4())
    conn.execute(
        "INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (message_id, idem, "OUTBOX", sender, recipient, kind, digest, str(payload_path), "ACKED", utcnow(), transport_actor, utcnow()),
    )
    conn.commit()
    row = conn.execute("SELECT message_id FROM messages WHERE idempotency_key=?", (idem,)).fetchone()
    return row[0]


def transition(fsm: UWSFSM, ctx: Any, state: FSMState, payload: dict[str, Any], source: str) -> str:
    event = fsm.transition(ctx, state, payload, source=source, parent_event_id=ctx.last_event.event_id if ctx.last_event else None)
    with sqlite3.connect(DB) as conn:
        set_meta(conn, "fsm_state", state.value)
        set_meta(conn, "last_event_id", event.event_id)
    return event.event_id


def rebuild_context(ledger: Ledger) -> Any:
    ctx = create_initial_context(TASK_ID, RUN_ID, 1, CORRELATION_ID, ledger)
    events = ledger.get_events_for_task_run(TASK_ID, RUN_ID)
    if events:
        ctx.last_event = events[-1]
        state = get_meta(sqlite3.connect(DB), "fsm_state", FSMState.TASK_ASSIGNED.value)
        ctx.current_state = FSMState(state)
        ctx.rework_round = sum(1 for e in events if e.event_type == EventType.REWORK_REQUIRED)
    return ctx


def validate_actor(result: dict[str, Any], actor: str) -> None:
    if result.get("task_id") != TASK_ID or result.get("correlation_id") != CORRELATION_ID or result.get("actor") != actor:
        raise ValueError(f"invalid correlated actor response from {actor}")


def run(stop_after_rejection: bool) -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "home").mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as conn:
        init_store(conn)
    ledger = Ledger(str(DB))
    fsm = UWSFSM(ledger)
    ctx = rebuild_context(ledger)

    try:
        with sqlite3.connect(DB) as conn:
            handoff_done = conn.execute("SELECT 1 FROM stage_runs WHERE stage='MORCH_HANDOFF' AND status='COMPLETED'").fetchone()
        if not handoff_done:
            prompt = f'''Act as MORCH. Return JSON only. Create a task handoff for a tiny Python palindrome library. Acceptance: is_palindrome(text) ignores case and all non-alphanumeric characters. Keys: task_id, correlation_id, actor, decision, requirements, max_rework_rounds, human_relay_count. Use task_id {TASK_ID}, correlation_id {CORRELATION_ID}, actor MORCH, decision TASK_HANDOFF, max_rework_rounds 2, human_relay_count 0.'''
            handoff = call_hermes("MORCH_HANDOFF", "MORCH", prompt)
            validate_actor(handoff, "MORCH")
            handoff_path = Path(sqlite3.connect(DB).execute("SELECT output_path FROM stage_runs WHERE stage='MORCH_HANDOFF'").fetchone()[0])
            with sqlite3.connect(DB) as conn:
                send_message(conn, "MORCH", "RORCH", "TASK_HANDOFF", handoff_path, "supervisor-spawn:MORCH")
            event = UWSEvent.create(EventType.TASK_HANDOFF, TASK_ID, RUN_ID, 1, CORRELATION_ID, "MORCH", handoff)
            ledger.append_event(event)
            transition(fsm, ctx, FSMState.RORCH_EXECUTING, {"task_handoff": handoff}, "UWS")

        with sqlite3.connect(DB) as conn:
            build_done = conn.execute("SELECT 1 FROM stage_runs WHERE stage='RORCH_BUILD' AND status='COMPLETED'").fetchone()
        if not build_done:
            prompt = f'''Act as RORCH in a controlled red-green E2E fixture. Return JSON only with task_id={TASK_ID}, correlation_id={CORRELATION_ID}, actor="RORCH", action="DELIVERY", and files containing exactly app.py and test_app.py as JSON strings. app.py must define is_palindrome(text). For the first delivery intentionally make the implementation case-insensitive but DO NOT remove punctuation (this is the seeded defect). test_app.py must use unittest and cover Racecar=True and hello=False only. No markdown.'''
            build = call_hermes("RORCH_BUILD", "RORCH", prompt)
            validate_actor(build, "RORCH")
        if ctx.current_state == FSMState.RORCH_EXECUTING:
            build_path = Path(sqlite3.connect(DB).execute("SELECT output_path FROM stage_runs WHERE stage='RORCH_BUILD'").fetchone()[0])
            bwrap("materialize", "/workspace/" + build_path.relative_to(FIXTURE).as_posix())
            local = EVIDENCE / "rorch_local_test_round1.json"
            bwrap("local-test", "/workspace/" + local.relative_to(FIXTURE).as_posix())
            transition(fsm, ctx, FSMState.SELF_TEST, {"execution_complete": True, "test_evidence": str(local)}, "RORCH")
            submission = QASubmission(task_id=TASK_ID, run_id=RUN_ID, attempt_id=1, correlation_id=CORRELATION_ID, parent_task_handoff_id="MORCH_HANDOFF", deliverables=[Deliverable("runtime/app/app.py", sha256(RUNTIME / "app" / "app.py"), "palindrome library")], evidence_manifest=EvidenceManifest(sha256(local), str(local), "NOT_RUN"), acceptance_criteria=[AcceptanceCriterion("AC-001", "Ignore case and non-alphanumeric characters", "NOT_EVALUATED", "")])
            transition(fsm, ctx, FSMState.SUBMITTED_FOR_QA, {"self_test_passed": True, "qa_submission": submission.to_dict()}, "RORCH")

        with sqlite3.connect(DB) as conn:
            qa1_done = conn.execute("SELECT 1 FROM stage_runs WHERE stage='QA_ROUND1' AND status='COMPLETED'").fetchone()
        if not qa1_done:
            transition(fsm, ctx, FSMState.QA_RUNNING, {"qa_submission": {"task_id": TASK_ID, "run_id": RUN_ID, "deliverables": ["runtime/app/app.py"], "evidence_manifest": {}, "acceptance_criteria": ["AC-001"]}}, "QA")
            probe1 = EVIDENCE / "qa_probe_round1.json"
            bwrap("qa-probe", "/workspace/" + probe1.relative_to(FIXTURE).as_posix(), expect=1)
            app_text = (RUNTIME / "app" / "app.py").read_text(encoding="utf-8")
            prompt = f'''Act as an independent QA reviewer with no prior RORCH context. Requirement: is_palindrome ignores case and all non-alphanumeric characters. Inspect this code:\n---\n{app_text}\n---\nDeterministic probe evidence:\n{probe1.read_text(encoding="utf-8")}\nReturn JSON only with task_id={TASK_ID}, correlation_id={CORRELATION_ID}, actor="QA", verdict="REJECTED", finding_id="QA-FINDING-001", expected, observed, reproducible_test, evidence_sha256="{sha256(probe1)}". Do not propose a pass.'''
            verdict1 = call_hermes("QA_ROUND1", "QA", prompt)
            validate_actor(verdict1, "QA")
            if verdict1.get("verdict") != "REJECTED":
                raise ValueError("QA round 1 failed to reject the reproducible defect")
            qa1_path = Path(sqlite3.connect(DB).execute("SELECT output_path FROM stage_runs WHERE stage='QA_ROUND1'").fetchone()[0])
            with sqlite3.connect(DB) as conn:
                send_message(conn, "QA", "RORCH", "QA_REJECTED", qa1_path, "supervisor-spawn:QA")
            review = IndependentReviewEvidence("HERMES_QA_PROCESS_ROUND1", str(qa1_path), ["NTV_FUNCTIONAL"], sha256(probe1), "FAIL")
            qv = QAVerdict(task_id=TASK_ID, run_id=RUN_ID, attempt_id=1, correlation_id=CORRELATION_ID, parent_qa_submission_id=ctx.last_event.event_id, verdict=QAVerdictType.REJECTED, findings=["QA-FINDING-001"], independent_review_evidence=review, qa_acceptance_criteria_met=False, escalation_required=False)
            transition(fsm, ctx, FSMState.QA_REJECTED, {"qa_verdict": qv.to_dict()}, "QA")
            set_meta(sqlite3.connect(DB), "controlled_interrupt_ready", "true")
            if stop_after_rejection:
                print(json.dumps({"status": "CONTROLLED_INTERRUPT", "after": "QA_REJECTED", "task_id": TASK_ID, "correlation_id": CORRELATION_ID}))
                return 75

        if ctx.current_state == FSMState.QA_REJECTED:
            response = ReworkResponse(task_id=TASK_ID, run_id=RUN_ID, attempt_id=1, correlation_id=CORRELATION_ID, parent_qa_finding_id="QA-FINDING-001", response_type=ReworkResponseType.FIXED, evidence={"qa_verdict": "QA_ROUND1"}, requires_morch_escalation=False)
            transition(fsm, ctx, FSMState.REWORK_REQUIRED, {"rework_response": response.to_dict()}, "UWS")
            transition(fsm, ctx, FSMState.RORCH_EXECUTING, {}, "UWS")

        with sqlite3.connect(DB) as conn:
            fix_done = conn.execute("SELECT 1 FROM stage_runs WHERE stage='RORCH_FIX' AND status='COMPLETED'").fetchone()
        if not fix_done:
            qa1_path = Path(sqlite3.connect(DB).execute("SELECT output_path FROM stage_runs WHERE stage='QA_ROUND1'").fetchone()[0])
            app_text = (RUNTIME / "app" / "app.py").read_text(encoding="utf-8")
            prompt = f'''Act as RORCH receiving automatic QA feedback. Current app.py:\n{app_text}\nQA verdict:\n{qa1_path.read_text(encoding="utf-8")}\nReturn JSON only with task_id={TASK_ID}, correlation_id={CORRELATION_ID}, actor="RORCH", action="REWORK_RESPONSE", files containing exactly corrected app.py and test_app.py. Correctly ignore every non-alphanumeric character and case, and add a regression unittest for "A man, a plan, a canal: Panama!". No markdown.'''
            fixed = call_hermes("RORCH_FIX", "RORCH", prompt)
            validate_actor(fixed, "RORCH")
            fix_path = Path(sqlite3.connect(DB).execute("SELECT output_path FROM stage_runs WHERE stage='RORCH_FIX'").fetchone()[0])
            bwrap("materialize", "/workspace/" + fix_path.relative_to(FIXTURE).as_posix())
            local2 = EVIDENCE / "rorch_local_test_round2.json"
            bwrap("local-test", "/workspace/" + local2.relative_to(FIXTURE).as_posix())
            transition(fsm, ctx, FSMState.SELF_TEST, {"execution_complete": True, "test_evidence": str(local2)}, "RORCH")
            submission2 = QASubmission(task_id=TASK_ID, run_id=RUN_ID, attempt_id=1, correlation_id=CORRELATION_ID, parent_task_handoff_id="MORCH_HANDOFF", deliverables=[Deliverable("runtime/app/app.py", sha256(RUNTIME / "app" / "app.py"), "corrected palindrome library")], evidence_manifest=EvidenceManifest(sha256(local2), str(local2), "NOT_RUN"), acceptance_criteria=[AcceptanceCriterion("AC-001", "Ignore case and non-alphanumeric characters", "MET", str(local2))])
            transition(fsm, ctx, FSMState.SUBMITTED_FOR_QA, {"self_test_passed": True, "qa_submission": submission2.to_dict()}, "RORCH")

        with sqlite3.connect(DB) as conn:
            qa2_done = conn.execute("SELECT 1 FROM stage_runs WHERE stage='QA_ROUND2' AND status='COMPLETED'").fetchone()
        if not qa2_done:
            transition(fsm, ctx, FSMState.QA_RUNNING, {"qa_submission": {"task_id": TASK_ID, "run_id": RUN_ID, "deliverables": ["runtime/app/app.py"], "evidence_manifest": {}, "acceptance_criteria": ["AC-001"]}}, "QA")
            probe2 = EVIDENCE / "qa_probe_round2.json"
            bwrap("qa-probe", "/workspace/" + probe2.relative_to(FIXTURE).as_posix(), expect=0)
            app_text = (RUNTIME / "app" / "app.py").read_text(encoding="utf-8")
            prompt = f'''Act as the independent QA reviewer for re-review. Requirement: ignore case and all non-alphanumeric characters. Code:\n{app_text}\nDeterministic probe evidence:\n{probe2.read_text(encoding="utf-8")}\nReturn JSON only with task_id={TASK_ID}, correlation_id={CORRELATION_ID}, actor="QA", verdict="PASS", acceptance_criteria_met=true, evidence_sha256="{sha256(probe2)}". No markdown.'''
            verdict2 = call_hermes("QA_ROUND2", "QA", prompt)
            validate_actor(verdict2, "QA")
            if verdict2.get("verdict") != "PASS" or not verdict2.get("acceptance_criteria_met"):
                raise ValueError("QA round 2 did not pass corrected artifact")
            qa2_path = Path(sqlite3.connect(DB).execute("SELECT output_path FROM stage_runs WHERE stage='QA_ROUND2'").fetchone()[0])
            review2 = IndependentReviewEvidence("HERMES_QA_PROCESS_ROUND2", str(qa2_path), ["NTV_FUNCTIONAL"], sha256(probe2), "PASS")
            qv2 = QAVerdict(task_id=TASK_ID, run_id=RUN_ID, attempt_id=1, correlation_id=CORRELATION_ID, parent_qa_submission_id=ctx.last_event.event_id, verdict=QAVerdictType.PASS, findings=[], independent_review_evidence=review2, qa_acceptance_criteria_met=True, escalation_required=False)
            transition(fsm, ctx, FSMState.QA_PASS, {"qa_verdict": qv2.to_dict()}, "QA")
            with sqlite3.connect(DB) as conn:
                send_message(conn, "QA", "MORCH", "QA_PASS", qa2_path, "supervisor-spawn:QA")

        if ctx.current_state == FSMState.QA_PASS:
            transition(fsm, ctx, FSMState.MORCH_REVIEW, {"evidence_manifest_complete": True, "lease_active": True}, "UWS")

        with sqlite3.connect(DB) as conn:
            morch_done = conn.execute("SELECT 1 FROM stage_runs WHERE stage='MORCH_DECISION' AND status='COMPLETED'").fetchone()
        if not morch_done:
            qa2_path = Path(sqlite3.connect(DB).execute("SELECT output_path FROM stage_runs WHERE stage='QA_ROUND2'").fetchone()[0])
            prompt = f'''Act as MORCH receiving QA automatically. QA verdict:\n{qa2_path.read_text(encoding="utf-8")}\nReturn JSON only with task_id={TASK_ID}, correlation_id={CORRELATION_ID}, actor="MORCH", decision="ACCEPTED", human_relay_count=0, evidence_received=true.'''
            decision = call_hermes("MORCH_DECISION", "MORCH", prompt)
            validate_actor(decision, "MORCH")
            if decision.get("decision") != "ACCEPTED":
                raise ValueError("MORCH did not accept QA evidence")
            decision_path = Path(sqlite3.connect(DB).execute("SELECT output_path FROM stage_runs WHERE stage='MORCH_DECISION'").fetchone()[0])
            with sqlite3.connect(DB) as conn:
                send_message(conn, "MORCH", "RORCH", "ACCEPTED", decision_path, "supervisor-spawn:MORCH")
            transition(fsm, ctx, FSMState.ACCEPTED, {"morch_decision": "ACCEPTED"}, "MORCH")

        with sqlite3.connect(DB) as conn:
            ack_done = conn.execute("SELECT 1 FROM stage_runs WHERE stage='RORCH_ACK' AND status='COMPLETED'").fetchone()
        if not ack_done:
            decision_path = Path(sqlite3.connect(DB).execute("SELECT output_path FROM stage_runs WHERE stage='MORCH_DECISION'").fetchone()[0])
            prompt = f'''Act as RORCH receiving MORCH decision automatically. Decision:\n{decision_path.read_text(encoding="utf-8")}\nReturn JSON only with task_id={TASK_ID}, correlation_id={CORRELATION_ID}, actor="RORCH", acknowledged=true, decision="ACCEPTED", human_relay_count=0.'''
            ack = call_hermes("RORCH_ACK", "RORCH", prompt)
            validate_actor(ack, "RORCH")
            if not ack.get("acknowledged"):
                raise ValueError("RORCH did not acknowledge final decision")
            transition(fsm, ctx, FSMState.TERMINAL_COMPLETED, {"terminal_manifest_complete": True}, "RORCH")

        integrity = ledger.verify_integrity(TASK_ID, RUN_ID)
        with sqlite3.connect(DB) as conn:
            stages = conn.execute("SELECT stage,actor,status,model,provider,session_id FROM stage_runs ORDER BY started_at").fetchall()
            messages = conn.execute("SELECT sender,recipient,message_type,status,idempotency_key FROM messages ORDER BY created_at").fetchall()
        result = {
            "status": "FUNCTIONAL_E2E_PASS",
            "task_id": TASK_ID,
            "run_id": RUN_ID,
            "correlation_id": CORRELATION_ID,
            "fsm_state": ctx.current_state.value,
            "ledger_integrity": integrity,
            "human_relay_count": 0,
            "stages": stages,
            "messages": messages,
            "isolation": "BWRAP_FOR_CODE_AND_TESTS;HERMES_BROKER_OUTSIDE_GUEST",
            "multimodel": "UNVERIFIED_SINGLE_CONFIGURED_MODEL",
        }
        result_path = EVIDENCE / "functional_result.json"
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps({"status": result["status"], "evidence": str(result_path), "fsm_state": result["fsm_state"]}))
        return 0
    finally:
        ledger.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop-after-rejection", action="store_true")
    args = parser.parse_args()
    return run(args.stop_after_rejection)


if __name__ == "__main__":
    raise SystemExit(main())

