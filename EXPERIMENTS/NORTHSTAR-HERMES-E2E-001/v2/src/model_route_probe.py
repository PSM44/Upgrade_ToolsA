"""Secret-zero, non-inference model-route readiness probe.

This probe never loads provider credentials and never contacts a provider.  It
combines declarative routes, persisted provider receipts and an optional
redacted runtime observation.  A declared label never becomes verified model
identity without a successful attested receipt in the router database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def probe(config_path: Path, db_path: Path | None = None,
          runtime_evidence: Path | None = None) -> dict[str, Any]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    routes: list[dict[str, Any]] = []
    for role, entries in raw.get("roles", {}).items():
        for entry in entries:
            routes.append({"selector": role.upper(), **entry})
    for selector, entries in raw.get("task_routes", {}).items():
        for entry in entries:
            routes.append({"selector": selector.upper(), **entry})

    verified: list[dict[str, Any]] = []
    if db_path is not None and db_path.exists():
        with closing(sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)) as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_receipts'"
            ).fetchone()
            if table:
                rows = conn.execute(
                    "SELECT DISTINCT actual_provider,actual_model FROM model_receipts "
                    "WHERE identity_status='VERIFIED_PROVIDER_RECEIPT' ORDER BY 1,2"
                ).fetchall()
                verified = [{"provider": p, "model": m} for p, m in rows]

    runtime: dict[str, Any] | None = None
    if runtime_evidence is not None and runtime_evidence.exists():
        observed = json.loads(runtime_evidence.read_text(encoding="utf-8"))
        runtime = {
            "source_sha256": sha256(runtime_evidence),
            "hermes_version": observed.get("hermes", {}).get("version"),
            "configured_provider": observed.get("hermes", {}).get("provider"),
            "configured_model": observed.get("hermes", {}).get("model"),
            "openrouter_configured": observed.get("openrouter", {}).get("configured"),
            "fallbacks": observed.get("fallbacks", {}).get("configured", []),
        }

    declared = sorted({(str(r.get("provider")), str(r.get("model"))) for r in routes})
    return {
        "schema": "northstar.model-route-readiness/v1",
        "probe_mode": "OFFLINE_NO_INFERENCE_NO_SECRET_READ",
        "config_sha256": sha256(config_path),
        "configured_routes": routes,
        "declared_identities": [{"provider": p, "model": m} for p, m in declared],
        "verified_identities": verified,
        "real_multimodel_verified": len(verified) >= 2,
        "runtime_observation": runtime,
        "status": "REAL_MULTIMODEL_VERIFIED" if len(verified) >= 2 else "MULTIMODEL_UNVERIFIED",
        "limitations": [
            "Declarative provider/model labels are not identity evidence.",
            "This probe performs no provider request and reads no credential value.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--runtime-evidence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = probe(args.config, args.db, args.runtime_evidence)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

