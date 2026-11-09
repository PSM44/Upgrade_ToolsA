#!/usr/bin/env python3
"""Durable, fail-closed NorthStar mission checkpoint and resume guard.

The checkpoint is an operational recovery document, not an authority source.
It never relaxes HUMAN gates, never starts a host service, and only executes an
explicit argv whose Python entrypoint and state file resolve inside the
authorized fixture.  JSON writes are atomic and self-authenticated with a
canonical SHA-256 digest.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "upgrade-ta.northstar-state/v1"
HASH_FIELD = "checkpoint_sha256"
REQUIRED_KEYS = frozenset({
    "schema_version", "mission_id", "objective_ids", "workspace_root",
    "source_commit", "source_artifacts", "completed_requirements",
    "remaining_tasks", "next_action", "running_processes", "recovery",
    "tests", "external_dependencies", "security_restrictions",
    "last_independently_verified", "revision", "updated_at", HASH_FIELD,
})


class CheckpointError(RuntimeError):
    """Checkpoint failed integrity, scope, authority or recovery validation."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def checkpoint_digest(value: Mapping[str, Any]) -> str:
    material = copy.deepcopy(dict(value))
    material[HASH_FIELD] = ""
    return hashlib.sha256(_canonical(material)).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attrs = path.lstat().st_file_attributes  # Windows only
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _inside(root: Path, candidate: Path, *, must_exist: bool = True) -> Path:
    root = root.resolve(strict=True)
    if must_exist:
        resolved = candidate.resolve(strict=True)
    else:
        resolved = candidate.parent.resolve(strict=True) / candidate.name
    if resolved != root and root not in resolved.parents:
        raise CheckpointError(f"path escapes authorized root: {candidate}")
    current = root
    relative = resolved.relative_to(root)
    for component in relative.parts:
        current = current / component
        if current.exists() and _is_reparse(current):
            raise CheckpointError(f"reparse/symlink path is not allowed: {current}")
    return resolved


def write_checkpoint(path: str | Path, state: Mapping[str, Any]) -> dict[str, Any]:
    """Atomically replace a checkpoint after validating its authorized root."""
    destination = Path(path)
    value = copy.deepcopy(dict(state))
    value.setdefault("schema_version", SCHEMA_VERSION)
    value.setdefault("revision", 0)
    value["revision"] = int(value["revision"]) + 1
    value["updated_at"] = _now()
    value[HASH_FIELD] = ""
    value[HASH_FIELD] = checkpoint_digest(value)

    root = Path(str(value.get("workspace_root", "")))
    if not root.is_absolute() or not root.is_dir():
        raise CheckpointError("workspace_root must be an existing absolute directory")
    _inside(root, destination, must_exist=destination.exists())

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp",
                                     dir=str(destination.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        try:
            directory_fd = os.open(destination.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            except OSError:
                pass
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return value


def load_checkpoint(path: str | Path, *, verify_artifacts: bool = True) -> dict[str, Any]:
    checkpoint_path = Path(path).resolve(strict=True)
    try:
        value = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise CheckpointError(f"checkpoint is unreadable: {exc}") from exc
    missing = sorted(REQUIRED_KEYS - value.keys())
    if missing:
        raise CheckpointError(f"checkpoint fields missing: {', '.join(missing)}")
    if value["schema_version"] != SCHEMA_VERSION:
        raise CheckpointError("unsupported checkpoint schema")
    if not isinstance(value["revision"], int) or value["revision"] < 1:
        raise CheckpointError("checkpoint revision must be positive")
    if not value["mission_id"] or not isinstance(value["objective_ids"], list):
        raise CheckpointError("mission/objective identity is invalid")
    if checkpoint_digest(value) != value[HASH_FIELD]:
        raise CheckpointError("checkpoint SHA-256 mismatch")

    root = Path(value["workspace_root"])
    if not root.is_absolute() or not root.is_dir():
        raise CheckpointError("authorized workspace root is unavailable")
    _inside(root, checkpoint_path)
    restrictions = value["security_restrictions"]
    if restrictions.get("production_uta006_hold_active") is not True:
        raise CheckpointError("UTA006 HUMAN_HOLD must remain active")
    if restrictions.get("allow_production_service_restart") is not False:
        raise CheckpointError("production service restart must remain forbidden")
    if Path(restrictions.get("allowed_write_root", "")).resolve() != root.resolve():
        raise CheckpointError("allowed_write_root differs from workspace_root")

    if verify_artifacts:
        artifacts = value["source_artifacts"]
        if not isinstance(artifacts, dict) or not artifacts:
            raise CheckpointError("source_artifacts must pin at least one file")
        for relative, expected in artifacts.items():
            artifact = _inside(root, root / relative)
            if len(str(expected)) != 64 or file_sha256(artifact) != expected:
                raise CheckpointError(f"source artifact hash mismatch: {relative}")
    return value


def _resume_argv(state: Mapping[str, Any], checkpoint_path: Path) -> list[str]:
    root = Path(state["workspace_root"]).resolve(strict=True)
    action = state["next_action"]
    argv = action.get("command") if isinstance(action, dict) else None
    if not isinstance(argv, list) or len(argv) < 2 or not all(isinstance(x, str) for x in argv):
        raise CheckpointError("next_action.command must be an explicit argv")
    if argv[0] != "{python}":
        raise CheckpointError("only the pinned Python runtime placeholder is allowed")
    script = _inside(root, root / argv[1])
    if script.suffix.lower() != ".py":
        raise CheckpointError("resume entrypoint must be a Python file")
    rendered = [sys.executable, str(script)]
    for item in argv[2:]:
        rendered.append(str(checkpoint_path) if item == "{checkpoint}" else item)
    return rendered


def resume(path: str | Path, *, dry_run: bool = False) -> int | list[str]:
    """Validate and run the next action in foreground, never via a shell/service."""
    checkpoint_path = Path(path).resolve(strict=True)
    state = load_checkpoint(checkpoint_path)
    if state["running_processes"]:
        raise CheckpointError("checkpoint reports active processes; reconcile before resume")
    argv = _resume_argv(state, checkpoint_path)
    if dry_run:
        return argv
    completed = subprocess.run(argv, cwd=state["workspace_root"], shell=False,
                               check=False)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        state = load_checkpoint(args.checkpoint)
        if args.validate_only:
            print(json.dumps({"status": "VALID", "mission_id": state["mission_id"],
                              "revision": state["revision"],
                              "checkpoint_sha256": state[HASH_FIELD]}, sort_keys=True))
            return 0
        result = resume(args.checkpoint, dry_run=args.dry_run)
        if args.dry_run:
            print(json.dumps({"status": "DRY_RUN_VALID", "argv": result}, sort_keys=True))
            return 0
        return int(result)
    except CheckpointError as exc:
        print(json.dumps({"status": "REFUSED", "error": str(exc)}, sort_keys=True),
              file=sys.stderr)
        return 64


if __name__ == "__main__":
    raise SystemExit(main())

