"""Deterministic operational memory and MORCH activation for NorthStar v2.

This module intentionally does not pretend that ChatGPT is a listener. Durable
QA_PASS events activate the explicitly named ``MORCH_LOCAL_ADAPTER`` component.
SQLite is the only state owner; every memory record participates in a SHA-256
chain and actor permissions are enforced before the transaction begins.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class MemoryErrorBase(RuntimeError):
    """Base error for deterministic memory failures."""


class AuthorizationError(MemoryErrorBase):
    """Actor is not allowed to write the requested record type."""


class IntegrityError(MemoryErrorBase):
    """The append-only hash chain or payload digest is invalid."""


class CursorError(MemoryErrorBase):
    """A consumer cursor update is invalid or regressive."""


ALLOWED_ACTORS: dict[str, frozenset[str]] = {
    "OBJECTIVE": frozenset({"MORCH_LOCAL_ADAPTER", "MORCH_LOCAL_DELEGATE"}),
    "DECISION": frozenset({"MORCH_LOCAL_ADAPTER", "MORCH_LOCAL_DELEGATE"}),
    "DEV_CONTEXT": frozenset({"RORCH"}),
    "CHECKPOINT": frozenset({"UWS"}),
    "QA_EVENT": frozenset({"QA"}),
    "MORCH_ACTIVATION": frozenset({"UWS"}),
    "ERROR": frozenset({"UWS", "RORCH", "QA", "MORCH_LOCAL_ADAPTER", "MORCH_LOCAL_DELEGATE"}),
    "RESULT": frozenset({"UWS", "RORCH", "QA", "MORCH_LOCAL_ADAPTER", "MORCH_LOCAL_DELEGATE"}),
}


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS operational_memory(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  record_id TEXT NOT NULL UNIQUE,
  task_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  actor TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  previous_chain_sha256 TEXT NOT NULL,
  chain_sha256 TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_task_seq ON operational_memory(task_id, seq);
CREATE TABLE IF NOT EXISTS consumer_cursors(
  consumer TEXT NOT NULL,
  task_id TEXT NOT NULL,
  last_seq INTEGER NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(consumer, task_id)
);
CREATE TABLE IF NOT EXISTS morch_activations(
  trigger_event_id TEXT PRIMARY KEY,
  memory_seq INTEGER NOT NULL UNIQUE,
  task_id TEXT NOT NULL,
  status TEXT NOT NULL,
  authority_component TEXT NOT NULL,
  limitation TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(memory_seq) REFERENCES operational_memory(seq)
);
"""


@dataclass(frozen=True)
class MemoryRecord:
    seq: int
    record_id: str
    task_id: str
    kind: str
    actor: str
    payload: dict[str, Any]
    payload_sha256: str
    previous_chain_sha256: str
    chain_sha256: str
    created_at: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class OperationalMemory:
    """Append-only task memory with transactional checkpoints and activations."""

    GENESIS = "0" * 64
    MORCH_COMPONENT = "MORCH_LOCAL_DELEGATE"
    MORCH_LIMITATION = "HUMAN-authorized local durable coordinator; not ChatGPT"

    def __init__(self, db_path: str | Path, *, read_only: bool = False):
        path = Path(db_path)
        self.db_path = str(path)
        target = f"file:{path.as_posix()}?mode=ro&immutable=1" if read_only else self.db_path
        self._conn = sqlite3.connect(
            target,
            timeout=30,
            isolation_level=None,
            uri=read_only,
        )
        self._conn.row_factory = sqlite3.Row
        if not read_only:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "OperationalMemory":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _authorize(kind: str, actor: str) -> None:
        if kind not in ALLOWED_ACTORS:
            raise AuthorizationError(f"unknown memory kind: {kind}")
        if actor not in ALLOWED_ACTORS[kind]:
            raise AuthorizationError(f"{actor} cannot write {kind}")

    def _last_chain(self, task_id: str) -> str:
        row = self._conn.execute(
            "SELECT chain_sha256 FROM operational_memory WHERE task_id=? ORDER BY seq DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        return row[0] if row else self.GENESIS

    def _append_in_transaction(
        self,
        *,
        task_id: str,
        kind: str,
        actor: str,
        payload: dict[str, Any],
        record_id: str | None = None,
    ) -> MemoryRecord:
        self._authorize(kind, actor)
        record_id = record_id or str(uuid.uuid4())
        payload_json = _canonical(payload)
        payload_sha = _digest(payload_json)
        previous = self._last_chain(task_id)
        created_at = _utcnow()
        chain_material = "|".join(
            (previous, record_id, task_id, kind, actor, payload_sha, created_at)
        )
        chain_sha = _digest(chain_material)
        cur = self._conn.execute(
            """INSERT INTO operational_memory
               (record_id,task_id,kind,actor,payload_json,payload_sha256,
                previous_chain_sha256,chain_sha256,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (record_id, task_id, kind, actor, payload_json, payload_sha,
             previous, chain_sha, created_at),
        )
        return MemoryRecord(
            cur.lastrowid, record_id, task_id, kind, actor, payload, payload_sha,
            previous, chain_sha, created_at,
        )

    def append(
        self,
        *,
        task_id: str,
        kind: str,
        actor: str,
        payload: dict[str, Any],
        record_id: str | None = None,
    ) -> MemoryRecord:
        self._authorize(kind, actor)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            result = self._append_in_transaction(
                task_id=task_id, kind=kind, actor=actor, payload=payload,
                record_id=record_id,
            )
            self._conn.commit()
            return result
        except Exception:
            self._conn.rollback()
            raise

    def checkpoint(
        self,
        *,
        task_id: str,
        state: str,
        event_id: str,
        context: dict[str, Any],
        simulate_crash: bool = False,
    ) -> MemoryRecord:
        """Atomically persist a checkpoint; crash injection proves rollback."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            result = self._append_in_transaction(
                task_id=task_id,
                kind="CHECKPOINT",
                actor="UWS",
                payload={"state": state, "event_id": event_id, "context": context},
            )
            if simulate_crash:
                raise RuntimeError("simulated crash before checkpoint commit")
            self._conn.commit()
            return result
        except Exception:
            self._conn.rollback()
            raise

    def activate_morch_from_qa_pass(
        self,
        *,
        task_id: str,
        event_id: str,
        event_type: str,
        source: str,
        payload: dict[str, Any],
    ) -> MemoryRecord:
        """Persist QA event and activate the local MORCH consumer atomically.

        Replaying the exact event is idempotent. A reused event id with a
        different payload is an integrity conflict.
        """
        if event_type != "QA_PASS" or source != "QA":
            raise AuthorizationError("MORCH activation requires a QA-sourced QA_PASS")
        event_payload = {
            "event_id": event_id,
            "event_type": event_type,
            "source": source,
            "payload": payload,
        }
        event_digest = _digest(_canonical(event_payload))
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            prior = self._conn.execute(
                """SELECT m.* FROM morch_activations a
                   JOIN operational_memory m ON m.seq=a.memory_seq
                   WHERE a.trigger_event_id=?""",
                (event_id,),
            ).fetchone()
            if prior:
                prior_payload = json.loads(prior["payload_json"])
                if prior_payload.get("trigger_event_sha256") != event_digest:
                    raise IntegrityError("event id replayed with different payload")
                self._conn.commit()
                return self._row(prior)

            self._append_in_transaction(
                task_id=task_id,
                kind="QA_EVENT",
                actor="QA",
                payload=event_payload,
                record_id=f"qa:{event_id}",
            )
            activation_payload = {
                "trigger_event_id": event_id,
                "trigger_event_sha256": event_digest,
                "authority_component": self.MORCH_COMPONENT,
                "limitation": self.MORCH_LIMITATION,
                "human_relay_count": 0,
                "status": "READY",
            }
            activation = self._append_in_transaction(
                task_id=task_id,
                kind="MORCH_ACTIVATION",
                actor="UWS",
                payload=activation_payload,
                record_id=f"morch-activation:{event_id}",
            )
            self._conn.execute(
                """INSERT INTO morch_activations
                   (trigger_event_id,memory_seq,task_id,status,authority_component,
                    limitation,created_at) VALUES(?,?,?,?,?,?,?)""",
                (event_id, activation.seq, task_id, "READY", self.MORCH_COMPONENT,
                 self.MORCH_LIMITATION, activation.created_at),
            )
            self._conn.commit()
            return activation
        except Exception:
            self._conn.rollback()
            raise

    def consume_morch_activation(self, event_id: str) -> dict[str, Any]:
        """Claim activation for the real local coordinator component."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT * FROM morch_activations WHERE trigger_event_id=?",
                (event_id,),
            ).fetchone()
            if not row:
                raise KeyError(event_id)
            if row["status"] == "READY":
                self._conn.execute(
                    "UPDATE morch_activations SET status='CONSUMED' WHERE trigger_event_id=? AND status='READY'",
                    (event_id,),
                )
            self._conn.commit()
            return {
                "trigger_event_id": event_id,
                "status": "CONSUMED",
                "authority_component": row["authority_component"],
                "limitation": row["limitation"],
                "human_relay_count": 0,
            }
        except Exception:
            self._conn.rollback()
            raise

    def set_cursor(self, *, consumer: str, task_id: str, last_seq: int) -> None:
        maximum = self._conn.execute(
            "SELECT COALESCE(MAX(seq),0) FROM operational_memory WHERE task_id=?",
            (task_id,),
        ).fetchone()[0]
        current_row = self._conn.execute(
            "SELECT last_seq FROM consumer_cursors WHERE consumer=? AND task_id=?",
            (consumer, task_id),
        ).fetchone()
        current = current_row[0] if current_row else 0
        if last_seq < current:
            raise CursorError("consumer cursor cannot move backward")
        if last_seq > maximum:
            raise CursorError("consumer cursor cannot exceed durable memory")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                """INSERT INTO consumer_cursors(consumer,task_id,last_seq,updated_at)
                   VALUES(?,?,?,?) ON CONFLICT(consumer,task_id) DO UPDATE SET
                   last_seq=excluded.last_seq,updated_at=excluded.updated_at""",
                (consumer, task_id, last_seq, _utcnow()),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def get_cursor(self, *, consumer: str, task_id: str) -> int:
        row = self._conn.execute(
            "SELECT last_seq FROM consumer_cursors WHERE consumer=? AND task_id=?",
            (consumer, task_id),
        ).fetchone()
        return row[0] if row else 0

    def records(self, task_id: str, after_seq: int = 0) -> list[MemoryRecord]:
        rows = self._conn.execute(
            "SELECT * FROM operational_memory WHERE task_id=? AND seq>? ORDER BY seq",
            (task_id, after_seq),
        ).fetchall()
        return [self._row(row) for row in rows]

    def search(self, task_id: str, query: str, *, kinds: Iterable[str] | None = None,
               limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve relevant durable decisions/context using deterministic token overlap.

        This is deliberately local and inspectable; it is retrieval, not an LLM
        claim.  Results include score and chain identity so callers can verify
        every returned memory record after a process restart.
        """
        tokens = set(re.findall(r"[a-z0-9_]+", query.lower()))
        if not tokens or limit < 1:
            return []
        allowed = set(kinds) if kinds is not None else None
        ranked: list[tuple[int, int, MemoryRecord]] = []
        for record in self.records(task_id):
            if allowed is not None and record.kind not in allowed:
                continue
            haystack = _canonical(record.payload).lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                ranked.append((score, record.seq, record))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [{"score": score, "seq": record.seq, "kind": record.kind,
                 "actor": record.actor, "payload": record.payload,
                 "chain_sha256": record.chain_sha256}
                for score, _, record in ranked[:limit]]

    def search_all(self, query: str, *, kinds: Iterable[str] | None = None,
                   limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve relevant memory across objectives without changing UWS ownership."""
        task_ids = [row[0] for row in self._conn.execute(
            "SELECT DISTINCT task_id FROM operational_memory ORDER BY task_id"
        )]
        combined: list[dict[str, Any]] = []
        for task_id in task_ids:
            for item in self.search(task_id, query, kinds=kinds, limit=limit):
                combined.append({"task_id": task_id, **item})
        combined.sort(key=lambda item: (item["score"], item["seq"]), reverse=True)
        return combined[:limit]

    def recover(self, task_id: str) -> dict[str, Any]:
        rows = self.records(task_id)
        checkpoints = [r for r in rows if r.kind == "CHECKPOINT"]
        latest = checkpoints[-1] if checkpoints else None
        after = [r for r in rows if latest is None or r.seq > latest.seq]
        return {
            "task_id": task_id,
            "checkpoint": latest.payload if latest else None,
            "checkpoint_seq": latest.seq if latest else 0,
            "records_after_checkpoint": [r.__dict__ for r in after],
            "chain_valid": self.verify_chain(task_id),
        }

    def verify_chain(self, task_id: str) -> bool:
        previous = self.GENESIS
        for row in self._conn.execute(
            "SELECT * FROM operational_memory WHERE task_id=? ORDER BY seq", (task_id,)
        ):
            payload_sha = _digest(row["payload_json"])
            if payload_sha != row["payload_sha256"]:
                raise IntegrityError(f"payload digest mismatch at seq {row['seq']}")
            if row["previous_chain_sha256"] != previous:
                raise IntegrityError(f"chain predecessor mismatch at seq {row['seq']}")
            material = "|".join(
                (previous, row["record_id"], row["task_id"], row["kind"],
                 row["actor"], payload_sha, row["created_at"])
            )
            expected = _digest(material)
            if expected != row["chain_sha256"]:
                raise IntegrityError(f"chain digest mismatch at seq {row['seq']}")
            previous = expected
        return True

    @staticmethod
    def _row(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            row["seq"], row["record_id"], row["task_id"], row["kind"],
            row["actor"], json.loads(row["payload_json"]), row["payload_sha256"],
            row["previous_chain_sha256"], row["chain_sha256"], row["created_at"],
        )


