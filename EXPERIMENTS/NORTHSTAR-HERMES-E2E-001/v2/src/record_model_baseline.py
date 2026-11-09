#!/usr/bin/env python3
"""Import pinned real Hermes results through the config-driven model router."""

from __future__ import annotations

import json
from pathlib import Path

from model_router import ModelRouter, Route

HERE = Path(__file__).resolve().parent
V2 = HERE.parent
BASE = V2.parent

ARTIFACTS = {
    "MORCH": (
        BASE / "runtime/actors/morch/morch_handoff/result.json",
        BASE / "runtime/actors/morch/morch_handoff/usage.json",
    ),
    "RORCH": (
        BASE / "runtime/actors/rorch/rorch_build/result.json",
        BASE / "runtime/actors/rorch/rorch_build/usage.json",
    ),
    "QA": (
        BASE / "runtime/actors/qa/qa_round1/result.json",
        BASE / "runtime/actors/qa/qa_round1/usage.json",
    ),
}


def backend(route: Route, prompt: str, context: dict) -> dict:
    role = str(context["role"])
    result_path, usage_path = ARTIFACTS[role]
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    return {
        "ok": True,
        "response": result_path.read_text(encoding="utf-8"),
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "api_calls": usage.get("api_calls", 0),
            "cost_status": usage.get("cost_status", "unknown"),
            "cost_usd": None,
        },
    }


def main() -> int:
    db = V2 / "runtime/state/model_routes.db"
    if db.exists():
        db = V2 / "runtime/state/model_routes_recheck.db"
    router = ModelRouter(V2 / "config/model_routes.json", db, {"baseline-replay": backend})
    invocations = [router.invoke(role, f"pinned baseline replay for {role}", {"role": role}) for role in ARTIFACTS]
    result = {
        "mode": "PINNED_REAL_HERMES_OUTPUT_REPLAY",
        "invocations": invocations,
        "successful_routes": sorted([list(v) for v in router.successful_routes()]),
        "multimodel_verified": router.multimodel_verified(),
        "usage": router.usage_summary(),
        "limitation": "No second authorized live model route is configured; replay does not prove a new inference call.",
    }
    output = V2 / "evidence/model_baseline_result.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "PASS", "multimodel_verified": result["multimodel_verified"], "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

