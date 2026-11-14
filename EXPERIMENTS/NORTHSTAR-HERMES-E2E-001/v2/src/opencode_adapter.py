#!/usr/bin/env python3
"""Constrained headless OpenCode adapter used by native Debian RORCH/QA workers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any


DENY_ENV = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "SSH_AUTH_SOCK",
    "GIT_ASKPASS",
}


def _json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("OpenCode response contained no JSON object")


def policy(role: str, model: str | None = None) -> dict[str, Any]:
    rules: list[dict[str, str]] = [
        {"action": "*", "resource": "*", "effect": "deny"},
        {"action": "read", "resource": "*", "effect": "allow"},
        {"action": "glob", "resource": "*", "effect": "allow"},
        {"action": "grep", "resource": "*", "effect": "allow"},
        {"action": "external_directory", "resource": "*", "effect": "deny"},
    ]
    if role == "RORCH":
        rules.extend(
            [
                {"action": "edit", "resource": "app.py", "effect": "allow"},
                {"action": "edit", "resource": "test_app.py", "effect": "allow"},
                {
                    "action": "shell",
                    "resource": "python3 -m unittest *",
                    "effect": "allow",
                },
            ]
        )
    result: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "permissions": rules,
    }
    if model and model.startswith("northstar/"):
        result["providers"] = {
            "northstar": {
                "name": "NorthStar secret-zero OmniRoute",
                "env": ["NORTHSTAR_BROKER_KEY"],
                "package": "@opencode/ai/providers/openai-compatible",
                "settings": {"baseURL": "http://127.0.0.1:22027/v1"},
                "models": {
                    "rorch": {
                        "modelID": "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
                        "name": "NorthStar RORCH",
                    },
                    "qa": {
                        "modelID": "openrouter/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                        "name": "NorthStar QA",
                    },
                },
            }
        }
    return result


class OpenCodeAdapter:
    def __init__(
        self,
        binary: Path,
        rorch_model: str,
        qa_model: str,
        timeout_seconds: int = 300,
    ) -> None:
        self.binary = binary
        self.models = {"RORCH": rorch_model, "QA": qa_model}
        self.timeout_seconds = timeout_seconds

    def invoke(
        self,
        *,
        actor: str,
        stage: str,
        location: Path,
        evidence_dir: Path,
        prompt: str,
        request_id: str,
    ) -> dict[str, Any]:
        if actor not in self.models:
            raise PermissionError(f"OpenCode actor denied: {actor}")
        location.mkdir(parents=True, exist_ok=True)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        config = location / "opencode.json"
        model = self.models[actor]
        config.write_text(
            json.dumps(policy(actor, model), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        config.chmod(0o600)

        command = [
            str(self.binary),
            "run",
            "--standalone",
            "--archived",
            "--format",
            "json",
            "--model",
            model,
            prompt,
        ]
        env = {k: v for k, v in os.environ.items() if k not in DENY_ENV}
        started = time.time()
        if model.startswith("northstar/"):
            env["NORTHSTAR_BROKER_KEY"] = "northstar-local-v1"
            warm = subprocess.run(
                [str(self.binary), "--standalone", "models"],
                cwd=location,
                env=env,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                timeout=30,
            )
            if warm.returncode or model not in warm.stdout.splitlines():
                raise RuntimeError(
                    f"OpenCode custom model prewarm failed: {(warm.stderr or warm.stdout)[-500:]}"
                )
            time.sleep(2.5)
        completed = subprocess.run(
            command,
            cwd=location,
            env=env,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
        )
        bootstrap_output = ""
        if completed.returncode and "provider.no-route" in (completed.stdout + completed.stderr):
            bootstrap_output = completed.stdout + completed.stderr
            time.sleep(2.5)
            completed = subprocess.run(
                command,
                cwd=location,
                env=env,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        trace_path = evidence_dir / f"{stage.lower()}_opencode.jsonl"
        trace_path.write_text(bootstrap_output + completed.stdout + completed.stderr, encoding="utf-8")

        events: list[dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

        texts = [
            str(event.get("part", {}).get("text", ""))
            for event in events
            if event.get("type") == "text"
        ]
        finishes = [
            event.get("part", {})
            for event in events
            if event.get("type") == "step_finish"
        ]
        cost = sum(float(part.get("cost") or 0) for part in finishes)
        tokens = {"input": 0, "output": 0, "reasoning": 0}
        for part in finishes:
            usage = part.get("tokens") or {}
            for key in tokens:
                tokens[key] += int(usage.get(key) or 0)

        receipt = {
            "request_id": request_id,
            "actor": actor,
            "stage": stage,
            "runtime": "opencode",
            "opencode_version": "2.2.7",
            "requested_model": model,
            "actual_model": model,
            "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
            "exit_code": completed.returncode,
            "cost_value": cost,
            "cost_status": "provider_reported",
            "tokens": tokens,
            "input_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
            "elapsed_seconds": round(time.time() - started, 6),
        }
        if completed.returncode:
            raise RuntimeError(
                f"OpenCode exit {completed.returncode}: "
                f"{(completed.stderr or completed.stdout)[-1000:]}"
            )
        return {
            "text": texts[-1] if texts else "",
            "json": _json_object(texts[-1]) if actor == "QA" and texts else None,
            "receipt": receipt,
            "trace": str(trace_path),
        }
