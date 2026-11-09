#!/usr/bin/env python3
"""Role-scoped worker intended to run inside a bubblewrap mount policy."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path


def materialize(source: Path, app_dir: Path) -> int:
    data = json.loads(source.read_text(encoding="utf-8"))
    files = data.get("files", {})
    if set(files) != {"app.py", "test_app.py"}:
        raise ValueError("delivery must contain exactly app.py and test_app.py")
    app_dir.mkdir(parents=True, exist_ok=True)
    for name in ("app.py", "test_app.py"):
        (app_dir / name).write_text(str(files[name]), encoding="utf-8", newline="\n")
    return 0


def local_test(app_dir: Path, output: Path) -> int:
    proc = subprocess.run(
        ["/usr/bin/python3", "-m", "unittest", "-v", "test_app.py"],
        cwd=app_dir,
        text=True,
        capture_output=True,
        timeout=30,
    )
    output.write_text(json.dumps({
        "actor": "RORCH", "command": "python -m unittest -v test_app.py",
        "exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr,
    }, indent=2, sort_keys=True), encoding="utf-8")
    return proc.returncode


def qa_probe(app_dir: Path, output: Path) -> int:
    spec = importlib.util.spec_from_file_location("fixture_app", app_dir / "app.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load app")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = [
        ("A man, a plan, a canal: Panama!", True),
        ("No 'x' in Nixon", True), ("OpenAI", False), ("!!!", True),
    ]
    rows = []
    passed = True
    for value, expected in cases:
        try:
            actual = bool(module.is_palindrome(value)); error = None
        except Exception as exc:
            actual = None; error = f"{type(exc).__name__}: {exc}"
        ok = error is None and actual == expected
        passed = passed and ok
        rows.append({"input": value, "expected": expected, "actual": actual, "error": error, "passed": ok})
    output.write_text(json.dumps({
        "actor": "QA", "probe": "QA-INDEPENDENT-PALINDROME-v2",
        "exit_code": 0 if passed else 1, "cases": rows,
    }, indent=2, sort_keys=True), encoding="utf-8")
    return 0 if passed else 1


def deny_probe(app_dir: Path, qa_dir: Path, state_dir: Path, output: Path) -> int:
    results = {}
    targets = {
        "qa_cannot_modify_app": app_dir / "qa-forbidden.txt",
        "qa_cannot_modify_state": state_dir / "qa-forbidden.txt",
        "qa_can_write_evidence": qa_dir / "qa-allowed.txt",
    }
    for label, target in targets.items():
        try:
            target.write_text(label, encoding="utf-8")
            results[label] = "WRITE_SUCCEEDED"
        except Exception as exc:
            results[label] = f"DENIED:{type(exc).__name__}"
    expected = (
        results["qa_cannot_modify_app"].startswith("DENIED:")
        and results["qa_cannot_modify_state"].startswith("DENIED:")
        and results["qa_can_write_evidence"] == "WRITE_SUCCEEDED"
    )
    output.write_text(json.dumps({"actor": "QA", "passed": expected, "results": results}, indent=2, sort_keys=True), encoding="utf-8")
    return 0 if expected else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    m = sub.add_parser("materialize"); m.add_argument("source", type=Path); m.add_argument("app_dir", type=Path)
    t = sub.add_parser("local-test"); t.add_argument("app_dir", type=Path); t.add_argument("output", type=Path)
    q = sub.add_parser("qa-probe"); q.add_argument("app_dir", type=Path); q.add_argument("output", type=Path)
    d = sub.add_parser("deny-probe"); d.add_argument("app_dir", type=Path); d.add_argument("qa_dir", type=Path); d.add_argument("state_dir", type=Path); d.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "materialize": return materialize(args.source, args.app_dir)
    if args.command == "local-test": return local_test(args.app_dir, args.output)
    if args.command == "qa-probe": return qa_probe(args.app_dir, args.output)
    return deny_probe(args.app_dir, args.qa_dir, args.state_dir, args.output)


if __name__ == "__main__":
    raise SystemExit(main())

