#!/usr/bin/env python3
"""Deterministic worker executed inside the networkless bubblewrap guest."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/workspace/runtime")
APP = ROOT / "app"


def materialize(source: Path) -> int:
    data = json.loads(source.read_text(encoding="utf-8"))
    files = data.get("files", {})
    required = {"app.py", "test_app.py"}
    if set(files) != required or not all(isinstance(files[n], str) for n in required):
        raise ValueError("actor response must contain exactly app.py and test_app.py")
    APP.mkdir(parents=True, exist_ok=True)
    for name in sorted(required):
        (APP / name).write_text(files[name], encoding="utf-8", newline="\n")
    return 0


def local_test(output: Path) -> int:
    proc = subprocess.run(
        ["/usr/bin/python3", "-m", "unittest", "-v", "test_app.py"],
        cwd=APP,
        text=True,
        capture_output=True,
        timeout=30,
    )
    result = {
        "command": "python -m unittest -v test_app.py",
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return proc.returncode


def qa_probe(output: Path) -> int:
    spec = importlib.util.spec_from_file_location("fixture_app", APP / "app.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load app")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = [
        ("A man, a plan, a canal: Panama!", True),
        ("No 'x' in Nixon", True),
        ("OpenAI", False),
        ("!!!", True),
    ]
    observed = []
    ok = True
    for value, expected in cases:
        try:
            actual = bool(module.is_palindrome(value))
            error = None
        except Exception as exc:  # evidence, not suppression
            actual = None
            error = f"{type(exc).__name__}: {exc}"
        passed = error is None and actual == expected
        ok = ok and passed
        observed.append({"input": value, "expected": expected, "actual": actual, "error": error, "passed": passed})
    result = {
        "probe": "QA-INDEPENDENT-PALINDROME-ACCEPTANCE-v1",
        "exit_code": 0 if ok else 1,
        "cases": observed,
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result["exit_code"]


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_materialize = sub.add_parser("materialize")
    p_materialize.add_argument("source", type=Path)
    p_test = sub.add_parser("local-test")
    p_test.add_argument("output", type=Path)
    p_qa = sub.add_parser("qa-probe")
    p_qa.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "materialize":
        return materialize(args.source)
    if args.command == "local-test":
        return local_test(args.output)
    return qa_probe(args.output)


if __name__ == "__main__":
    raise SystemExit(main())

