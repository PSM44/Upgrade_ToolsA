#!/usr/bin/env python3
"""Read-only verifier for the Debian OpenCode Go NorthStar run."""
from __future__ import annotations

import argparse
import json
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any

V2 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(V2 / "src"))
from operational_memory import OperationalMemory


def rows(db: Path, query: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, args)]


def check(name: str, passed: bool, detail: str, gate: str = "FUNCTIONAL") -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail, "gate": gate}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--objective-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--auth-files",
        nargs="*",
        type=Path,
        default=[
            Path("/home/psm/.local/share/opencode/opencode.db"),
            Path("/home/psm/.config/opencode/service.json"),
        ],
    )
    args = parser.parse_args()
    runtime = args.runtime
    objective_id = args.objective_id

    objective = rows(
        runtime / "control_plane.db",
        "select state,result_json from objectives where objective_id=?",
        (objective_id,),
    )
    dispatches = rows(
        runtime / "control_plane.db",
        "select message_id,actor,state,attempt,exit_code from dispatches "
        "where objective_id=? order by started_at",
        (objective_id,),
    )
    events = rows(
        runtime / "control_plane.db",
        "select event_type,detail_json from events order by seq",
    )
    outbox = rows(
        runtime / "transport.db",
        "select message_id,message_type,state,attempt_count,consumer_receipt "
        "from outbox order by created_at",
    )
    inbox = rows(
        runtime / "transport.db",
        "select message_id,state,consumer_receipt from inbox order by received_at",
    )
    calls = rows(
        runtime / "model_calls.db",
        "select request_id,actor,stage,requested_model,actual_provider,actual_model,"
        "session_id,status,input_tokens,output_tokens,total_tokens,cost_value,cost_status,"
        "cost_source,exit_code from calls order by created_at",
    )
    delegate = rows(
        runtime / "delegate.db",
        "select objective_id,status,result_json from requests where objective_id=?",
        (objective_id,),
    )
    qa_root = runtime / "objectives" / objective_id / "qa"
    qa1 = json.loads((qa_root / "qa_round1.json").read_text(encoding="utf-8"))
    qa2 = json.loads((qa_root / "qa_round2.json").read_text(encoding="utf-8"))
    result = json.loads(objective[0]["result_json"]) if objective else {}

    with OperationalMemory(runtime / "memory.db", read_only=True) as memory:
        memory_ok = memory.verify_chain(objective_id)
        recovered = memory.recover(objective_id)

    auth_modes = {
        str(path): stat.S_IMODE(path.stat().st_mode) if path.exists() else None
        for path in args.auth_files
    }
    expected_edges = [
        "TASK_HANDOFF",
        "DELIVERY",
        "QA_REJECTED",
        "REWORK_RESPONSE",
        "QA_PASS",
        "ACCEPTED",
        "RORCH_ACK",
    ]
    actual_models = sorted({row["actual_model"] for row in calls if row["actual_model"]})
    start_count = sum(event["event_type"] == "CONTROL_PLANE_STARTED" for event in events)
    failure_count = sum(event["event_type"] == "WORKER_FAILED" for event in events)
    total_tokens = sum(int(row["total_tokens"] or 0) for row in calls)
    reported_cost = sum(float(row["cost_value"] or 0) for row in calls)

    checks = [
        check(
            "morch_local_delegate_ingress_egress",
            len(delegate) == 1
            and delegate[0]["status"] == "COMPLETED"
            and result.get("status") == "ACCEPTED"
            and result.get("human_relay_count") == 0,
            "authenticated local delegate returned terminal result; it is not ChatGPT MORCH",
        ),
        check(
            "seven_edge_consumer_cycle",
            [row["message_type"] for row in outbox] == expected_edges
            and all(row["state"] == "ACKED" and row["consumer_receipt"] for row in outbox)
            and len(inbox) == 7
            and all(row["state"] == "ACKED" and row["consumer_receipt"] for row in inbox),
            "durable producer enqueue, consumer claim and consumer-owned ACK for seven edges",
        ),
        check(
            "qa_reject_fix_accept",
            qa1["exit_code"] != 0
            and qa1["model_verdict"]["verdict"] == "REJECTED"
            and qa2["exit_code"] == 0
            and qa2["model_verdict"]["verdict"] == "PASS",
            "separate QA guest rejected round 1 and accepted corrected round 2",
        ),
        check(
            "restart_lease_recovery",
            start_count >= 3
            and failure_count >= 2
            and next(row for row in dispatches if row["message_id"].endswith(":rework-2"))["attempt"] == 3,
            f"control_starts={start_count}; worker_failures={failure_count}; rework_attempt=3",
        ),
        check(
            "dispatch_terminal",
            len(dispatches) == 7
            and all(row["state"] == "COMPLETED" and row["exit_code"] == 0 for row in dispatches),
            "seven unique dispatches ended successfully after bounded recovery",
        ),
        check(
            "memory_integrity_retrieval",
            memory_ok and bool(recovered.get("checkpoint")),
            "hash chain verifies and semantic recovery returns a checkpoint",
        ),
        check(
            "two_real_go_models",
            actual_models
            == ["opencode-go/glm-5.3-flash", "opencode-go/mimo-v2.6-flash"]
            and all(
                row["actual_provider"] == "opencode-go"
                and row["session_id"]
                and row["status"] == "COMPLETED"
                and int(row["total_tokens"] or 0) > 0
                for row in calls
            ),
            f"actual_models={actual_models}; calls={len(calls)}; tokens={total_tokens}",
            "MULTIMODEL",
        ),
        check(
            "bounded_model_budget",
            len(calls) == 5 and len({row["request_id"] for row in calls}) == 5,
            "five completed calls under configured max_calls=6",
            "COST",
        ),
        check(
            "provider_reported_cost",
            all(
                row["cost_status"] == "provider_reported"
                and row["cost_source"] == "opencode_step_finish"
                and row["cost_value"] is not None
                for row in calls
            ),
            f"OpenCode step_finish aggregate={reported_cost}; Go allowance header not exposed by CLI",
            "COST",
        ),
        check(
            "go_auth_file_permissions",
            bool(auth_modes) and all(mode == 0o600 for mode in auth_modes.values()),
            f"modes={{{', '.join(f'{Path(k).name}:0o{v:o}' for k, v in auth_modes.items() if v is not None)}}}",
            "ISOLATION",
        ),
        check(
            "authoritative_allowance_and_extra_usage",
            False,
            "CLI step_finish reported cost=0 but did not expose opencode.allowance; Extra Usage OFF and $0 credit are HUMAN-provided policy evidence",
            "BILLING",
        ),
        check(
            "full_ntv_root",
            False,
            "bwrap guest and Debian process tree were inspected; WSL Debian is not full Windows host isolation",
            "ROOT",
        ),
        check(
            "independent_reviewer_authority",
            False,
            "mechanical verifier is a separate read-only process, but no separately authorized reviewer principal signed comprehensive acceptance",
            "INDEPENDENCE",
        ),
    ]
    functional_gates = {"FUNCTIONAL", "MULTIMODEL", "COST", "ISOLATION"}
    payload = {
        "schema": "upgrade-ta.northstar.opencode-go-acceptance/v1",
        "objective_id": objective_id,
        "runtime": str(runtime),
        "authority": "MORCH_LOCAL_DELEGATE_NOT_CHATGPT",
        "checks": checks,
        "functional_pass": all(
            item["passed"] for item in checks if item["gate"] in functional_gates
        ),
        "actual_models": actual_models,
        "model_calls": calls,
        "total_tokens": total_tokens,
        "provider_reported_cost": reported_cost,
        "auth_modes": {Path(path).name: mode for path, mode in auth_modes.items()},
        "status": "NORTHSTAR_PASS" if all(item["passed"] for item in checks) else "NORTHSTAR_FAIL",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "functional_pass": payload["functional_pass"],
                "passed": f"{sum(item['passed'] for item in checks)}/{len(checks)}",
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if payload["status"] == "NORTHSTAR_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
