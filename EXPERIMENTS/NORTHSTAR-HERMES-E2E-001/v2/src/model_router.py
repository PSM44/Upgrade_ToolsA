"""Config-driven model selection with bounded fallback and honest cost telemetry.

The router deliberately does not load provider credentials.  A caller registers
an already-authorized backend callable for each provider name.  Route selection,
attempt persistence and cost semantics remain deterministic and testable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


class RouterError(RuntimeError):
    """Base error for routing/configuration failures."""


class NoRouteError(RouterError):
    """No configured route can serve the requested role."""


class AllRoutesFailed(RouterError):
    """Every configured primary/fallback route failed."""

    def __init__(self, role: str, failures: list[dict[str, str]]):
        self.role = role
        self.failures = failures
        super().__init__(f"all routes failed for {role}: {failures}")


@dataclass(frozen=True)
class Route:
    provider: str
    model: str
    max_attempts: int = 1
    timeout_seconds: int = 120

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Route":
        provider = str(value.get("provider", "")).strip()
        model = str(value.get("model", "")).strip()
        max_attempts = int(value.get("max_attempts", 1))
        timeout_seconds = int(value.get("timeout_seconds", 120))
        if not provider or not model:
            raise ValueError("route requires non-empty provider and model")
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be in [1,5]")
        if not 1 <= timeout_seconds <= 1800:
            raise ValueError("timeout_seconds must be in [1,1800]")
        return cls(provider, model, max_attempts, timeout_seconds)


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    api_calls: int
    cost_status: str
    cost_usd: float | None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "Usage":
        raw = value or {}
        status = str(raw.get("cost_status", "unknown")).lower()
        if status not in {"measured", "estimated", "unknown"}:
            raise ValueError(f"invalid cost_status: {status}")
        input_tokens = int(raw.get("input_tokens", 0))
        output_tokens = int(raw.get("output_tokens", 0))
        api_calls = int(raw.get("api_calls", 1))
        if min(input_tokens, output_tokens, api_calls) < 0:
            raise ValueError("usage counters cannot be negative")
        cost = raw.get("cost_usd")
        if status == "unknown":
            # Unknown is represented as SQL NULL/Python None, never numeric zero.
            cost = None
        elif cost is None:
            raise ValueError(f"cost_usd required when cost_status={status}")
        else:
            cost = float(cost)
            if cost < 0:
                raise ValueError("cost_usd cannot be negative")
        return cls(input_tokens, output_tokens, api_calls, status, cost)


Backend = Callable[[Route, str, Mapping[str, Any]], Mapping[str, Any]]


class ModelRouter:
    """Selects per-role routes and persists every attempt in SQLite."""

    def __init__(
        self,
        config: str | Path | Mapping[str, Any],
        db_path: str | Path,
        backends: Mapping[str, Backend] | None = None,
    ) -> None:
        self.config = self._load_config(config)
        self.db_path = str(Path(db_path))
        self.backends: dict[str, Backend] = dict(backends or {})
        self._lock = threading.RLock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @staticmethod
    def _load_config(config: str | Path | Mapping[str, Any]) -> dict[str, list[Route]]:
        if isinstance(config, Mapping):
            raw = dict(config)
        else:
            raw = json.loads(Path(config).read_text(encoding="utf-8"))
        roles = raw.get("roles")
        if not isinstance(roles, Mapping) or not roles:
            raise ValueError("config requires non-empty roles mapping")
        parsed: dict[str, list[Route]] = {}
        for role, entries in roles.items():
            role_name = str(role).strip().upper()
            if not role_name or not isinstance(entries, list) or not entries:
                raise ValueError(f"role {role!r} requires at least one route")
            parsed[role_name] = [Route.from_dict(entry) for entry in entries]
        return parsed

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_invocations(
                  invocation_id TEXT PRIMARY KEY,
                  role TEXT NOT NULL,
                  prompt_sha256 TEXT NOT NULL,
                  selected_provider TEXT,
                  selected_model TEXT,
                  status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS model_attempts(
                  attempt_id TEXT PRIMARY KEY,
                  invocation_id TEXT NOT NULL REFERENCES model_invocations(invocation_id),
                  ordinal INTEGER NOT NULL,
                  provider TEXT NOT NULL,
                  model TEXT NOT NULL,
                  status TEXT NOT NULL,
                  error_type TEXT,
                  error_message TEXT,
                  input_tokens INTEGER,
                  output_tokens INTEGER,
                  api_calls INTEGER,
                  cost_status TEXT,
                  cost_usd REAL,
                  started_at TEXT NOT NULL,
                  completed_at TEXT NOT NULL,
                  UNIQUE(invocation_id, ordinal)
                );
                """
            )

    def register_backend(self, provider: str, backend: Backend) -> None:
        if not provider.strip() or not callable(backend):
            raise ValueError("provider and callable backend required")
        self.backends[provider] = backend

    def routes_for(self, role: str) -> tuple[Route, ...]:
        routes = self.config.get(role.strip().upper())
        if not routes:
            raise NoRouteError(f"no route configured for role {role}")
        return tuple(routes)

    def invoke(self, role: str, prompt: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        import hashlib

        role_name = role.strip().upper()
        routes = self.routes_for(role_name)
        invocation_id = str(uuid.uuid4())
        created = datetime.now(timezone.utc).isoformat()
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO model_invocations VALUES(?,?,?,?,?,?,?,?)",
                (invocation_id, role_name, prompt_hash, None, None, "RUNNING", created, None),
            )

        failures: list[dict[str, str]] = []
        ordinal = 0
        for route in routes:
            backend = self.backends.get(route.provider)
            for _ in range(route.max_attempts):
                ordinal += 1
                started = datetime.now(timezone.utc).isoformat()
                attempt_id = str(uuid.uuid4())
                try:
                    if backend is None:
                        raise NoRouteError(f"backend not registered: {route.provider}")
                    raw = dict(backend(route, prompt, dict(context or {})))
                    if raw.get("ok", True) is not True:
                        raise RuntimeError(str(raw.get("error", "backend returned ok=false")))
                    usage = Usage.from_dict(raw.get("usage"))
                    response = raw.get("response")
                    if not isinstance(response, str):
                        raise ValueError("backend response must be a string")
                    completed = datetime.now(timezone.utc).isoformat()
                    self._record_attempt(
                        attempt_id, invocation_id, ordinal, route, "SUCCESS", None, None,
                        usage, started, completed,
                    )
                    with self._lock, closing(self._connect()) as conn, conn:
                        conn.execute(
                            "UPDATE model_invocations SET selected_provider=?,selected_model=?,status='COMPLETED',completed_at=? WHERE invocation_id=?",
                            (route.provider, route.model, completed, invocation_id),
                        )
                    return {
                        "invocation_id": invocation_id,
                        "role": role_name,
                        "provider": route.provider,
                        "model": route.model,
                        "response": response,
                        "usage": {
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                            "api_calls": usage.api_calls,
                            "cost_status": usage.cost_status,
                            "cost_usd": usage.cost_usd,
                        },
                        "fallback_used": ordinal > 1,
                        "attempts": ordinal,
                    }
                except Exception as exc:  # persisted failure then bounded fallback
                    completed = datetime.now(timezone.utc).isoformat()
                    failure = {"provider": route.provider, "model": route.model, "error": f"{type(exc).__name__}: {exc}"}
                    failures.append(failure)
                    self._record_attempt(
                        attempt_id, invocation_id, ordinal, route, "FAILED",
                        type(exc).__name__, str(exc), None, started, completed,
                    )

        completed = datetime.now(timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE model_invocations SET status='FAILED',completed_at=? WHERE invocation_id=?",
                (completed, invocation_id),
            )
        raise AllRoutesFailed(role_name, failures)

    def _record_attempt(
        self,
        attempt_id: str,
        invocation_id: str,
        ordinal: int,
        route: Route,
        status: str,
        error_type: str | None,
        error_message: str | None,
        usage: Usage | None,
        started: str,
        completed: str,
    ) -> None:
        values = usage or Usage(0, 0, 0, "unknown", None)
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO model_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt_id, invocation_id, ordinal, route.provider, route.model, status,
                    error_type, error_message, values.input_tokens, values.output_tokens,
                    values.api_calls, values.cost_status, values.cost_usd, started, completed,
                ),
            )

    def successful_routes(self) -> set[tuple[str, str]]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT DISTINCT provider,model FROM model_attempts WHERE status='SUCCESS'"
            ).fetchall()
        return {(str(p), str(m)) for p, m in rows}

    def multimodel_verified(self) -> bool:
        return len(self.successful_routes()) >= 2

    def usage_summary(self) -> dict[str, Any]:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT input_tokens,output_tokens,api_calls,cost_status,cost_usd FROM model_attempts WHERE status='SUCCESS'"
            ).fetchall()
        statuses = sorted({r[3] for r in rows})
        known_costs = [float(r[4]) for r in rows if r[4] is not None]
        fully_known = bool(rows) and all(r[3] in {"measured", "estimated"} and r[4] is not None for r in rows)
        return {
            "successful_attempts": len(rows),
            "input_tokens": sum(int(r[0] or 0) for r in rows),
            "output_tokens": sum(int(r[1] or 0) for r in rows),
            "api_calls": sum(int(r[2] or 0) for r in rows),
            "cost_statuses": statuses,
            "cost_usd": sum(known_costs) if fully_known else None,
            "cost_complete": fully_known,
        }

