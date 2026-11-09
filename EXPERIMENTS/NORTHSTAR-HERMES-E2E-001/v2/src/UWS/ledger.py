"""
SQLite ledger operations for UWS MVP.
Implements append-only event log, artifact manifest, QA findings, rework rounds, and leases.
All operations are transactional with proper crash recovery.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from .models import (
    UWSEvent, EventType, QAFinding, FindingSeverity, QAVerdict, QAVerdictType,
    ReworkResponse, ReworkResponseType, ArtifactRecord, LeaseRecord, ReworkRound,
    TerminalEvent, QASubmission, TaskHandoff
)
from .idempotency import generate_idempotency_key


SCHEMA_VERSION = 1

SCHEMA_SQL = f"""
-- Schema version: {SCHEMA_VERSION}

-- Event log (immutable, append-only)
CREATE TABLE IF NOT EXISTS uws_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    attempt_id INTEGER NOT NULL,
    correlation_id TEXT NOT NULL,
    parent_event_id TEXT,
    timestamp_utc TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    lease_id TEXT,
    lease_expires_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_uws_events_task_run ON uws_events(task_id, run_id);
CREATE INDEX IF NOT EXISTS idx_uws_events_correlation ON uws_events(correlation_id);
CREATE INDEX IF NOT EXISTS idx_uws_events_idempotency ON uws_events(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_uws_events_timestamp ON uws_events(timestamp_utc);

-- Artifact manifest (hash-pinned with invalidation)
CREATE TABLE IF NOT EXISTS uws_artifacts (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    invalidated_at TEXT,
    invalidation_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_uws_artifacts_task_run ON uws_artifacts(task_id, run_id);
CREATE INDEX IF NOT EXISTS idx_uws_artifacts_path ON uws_artifacts(path);

-- QA findings (linked to QA events)
CREATE TABLE IF NOT EXISTS uws_qa_findings (
    finding_id TEXT PRIMARY KEY,
    parent_qa_event_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    path TEXT NOT NULL,
    expected TEXT NOT NULL,
    observed TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    acceptance_rule TEXT NOT NULL,
    reproducible_test TEXT NOT NULL,
    status TEXT DEFAULT 'OPEN',
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_uws_qa_findings_parent ON uws_qa_findings(parent_qa_event_id);

-- Rework rounds tracking (3-round cap)
CREATE TABLE IF NOT EXISTS uws_rework_rounds (
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    round_number INTEGER NOT NULL,
    rework_event_id TEXT NOT NULL,
    response_type TEXT NOT NULL,
    evidence_manifest_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, run_id, round_number)
);

-- Leases for crash recovery
CREATE TABLE IF NOT EXISTS uws_leases (
    lease_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    holder TEXT NOT NULL,
    state TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    heartbeat_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_uws_leases_task_run ON uws_leases(task_id, run_id);
CREATE INDEX IF NOT EXISTS idx_uws_leases_expires ON uws_leases(expires_at);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS uws_schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


class LedgerError(Exception):
    """Base exception for ledger operations."""
    pass


class DuplicateEventError(LedgerError):
    """Raised when attempting to insert duplicate event (idempotency violation)."""
    pass


class LeaseConflictError(LedgerError):
    """Raised when lease cannot be acquired due to conflict."""
    pass


class Ledger:
    """Thread-safe SQLite ledger for UWS event sourcing."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._initialize()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection with proper settings."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _initialize(self) -> None:
        """Initialize database schema."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.executescript(SCHEMA_SQL)
                # Check schema version
                cursor = conn.execute("SELECT version FROM uws_schema_version LIMIT 1")
                row = cursor.fetchone()
                if not row:
                    conn.execute(
                        "INSERT INTO uws_schema_version (version, applied_at) VALUES (?, ?)",
                        (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
                    )

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    @contextmanager
    def transaction(self):
        """Context manager for explicit transaction control."""
        conn = self._get_connection()
        with self._lock:
            with conn:
                yield conn

    # ========== Event Operations ==========

    def append_event(self, event: UWSEvent) -> UWSEvent:
        """
        Append event to ledger. Idempotent - raises DuplicateEventError if key exists.
        Uses BEGIN IMMEDIATE for crash safety.
        """
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute(
                        """INSERT INTO uws_events
                           (event_id, event_type, task_id, run_id, attempt_id, correlation_id,
                            parent_event_id, timestamp_utc, source, payload_sha256, payload_json,
                            idempotency_key, lease_id, lease_expires_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            event.event_id, event.event_type.value, event.task_id, event.run_id,
                            event.attempt_id, event.correlation_id, event.parent_event_id,
                            event.timestamp_utc, event.source, event.payload_sha256, event.payload_json,
                            event.idempotency_key, event.lease_id, event.lease_expires_at,
                        ),
                    )
            except sqlite3.IntegrityError as e:
                if "idempotency_key" in str(e) or "UNIQUE constraint failed" in str(e):
                    raise DuplicateEventError(f"Duplicate event: {event.idempotency_key}")
                raise
        return event

    def get_event(self, event_id: str) -> Optional[UWSEvent]:
        """Get event by ID."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM uws_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_event(row)

    def get_events_for_task_run(
        self, task_id: str, run_id: str, event_types: Optional[List[EventType]] = None
    ) -> List[UWSEvent]:
        """Get all events for a task/run, optionally filtered by type."""
        with self._lock:
            conn = self._get_connection()
            if event_types:
                placeholders = ",".join("?" * len(event_types))
                query = f"SELECT * FROM uws_events WHERE task_id = ? AND run_id = ? AND event_type IN ({placeholders}) ORDER BY timestamp_utc"
                params = [task_id, run_id] + [et.value for et in event_types]
            else:
                query = "SELECT * FROM uws_events WHERE task_id = ? AND run_id = ? ORDER BY timestamp_utc"
                params = [task_id, run_id]
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_event(row) for row in rows]

    def get_latest_event(self, task_id: str, run_id: str) -> Optional[UWSEvent]:
        """Get most recent event for task/run."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM uws_events WHERE task_id = ? AND run_id = ? ORDER BY timestamp_utc DESC LIMIT 1",
                (task_id, run_id),
            ).fetchone()
            if not row:
                return None
            return self._row_to_event(row)

    def get_event_by_idempotency_key(self, idempotency_key: str) -> Optional[UWSEvent]:
        """Get event by idempotency key (for duplicate detection)."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT * FROM uws_events WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if not row:
                return None
            return self._row_to_event(row)

    def _row_to_event(self, row: sqlite3.Row) -> UWSEvent:
        return UWSEvent(
            event_id=row["event_id"],
            event_type=EventType(row["event_type"]),
            task_id=row["task_id"],
            run_id=row["run_id"],
            attempt_id=row["attempt_id"],
            correlation_id=row["correlation_id"],
            parent_event_id=row["parent_event_id"],
            timestamp_utc=row["timestamp_utc"],
            source=row["source"],
            payload_sha256=row["payload_sha256"],
            payload_json=row["payload_json"],
            idempotency_key=row["idempotency_key"],
            lease_id=row["lease_id"],
            lease_expires_at=row["lease_expires_at"],
        )

    # ========== Artifact Operations ==========

    def insert_artifact(self, artifact: ArtifactRecord) -> ArtifactRecord:
        """Insert artifact record."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """INSERT INTO uws_artifacts
                       (artifact_id, task_id, run_id, path, sha256, description, created_at,
                        invalidated_at, invalidation_reason)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        artifact.artifact_id, artifact.task_id, artifact.run_id,
                        artifact.path, artifact.sha256, artifact.description,
                        artifact.created_at, artifact.invalidated_at, artifact.invalidation_reason,
                    ),
                )
        return artifact

    def get_artifacts(self, task_id: str, run_id: str, include_invalidated: bool = False) -> List[ArtifactRecord]:
        """Get all artifacts for task/run."""
        with self._lock:
            conn = self._get_connection()
            if include_invalidated:
                rows = conn.execute(
                    "SELECT * FROM uws_artifacts WHERE task_id = ? AND run_id = ? ORDER BY created_at",
                    (task_id, run_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM uws_artifacts WHERE task_id = ? AND run_id = ? AND invalidated_at IS NULL ORDER BY created_at",
                    (task_id, run_id),
                ).fetchall()
            return [self._row_to_artifact(row) for row in rows]

    def invalidate_artifact(self, artifact_id: str, reason: str) -> bool:
        """Mark artifact as invalidated."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute(
                    "UPDATE uws_artifacts SET invalidated_at = ?, invalidation_reason = ? WHERE artifact_id = ?",
                    (datetime.now(timezone.utc).isoformat(), reason, artifact_id),
                )
                return cursor.rowcount > 0

    def _row_to_artifact(self, row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            path=row["path"],
            sha256=row["sha256"],
            description=row["description"],
            created_at=row["created_at"],
            invalidated_at=row["invalidated_at"],
            invalidation_reason=row["invalidation_reason"],
        )

    # ========== QA Finding Operations ==========

    def insert_finding(self, finding: QAFinding) -> QAFinding:
        """Insert QA finding."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """INSERT INTO uws_qa_findings
                       (finding_id, parent_qa_event_id, severity, path, expected, observed,
                        evidence_hash, acceptance_rule, reproducible_test, status, resolved_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        finding.finding_id, finding.parent_qa_event_id, finding.severity.value,
                        finding.path, finding.expected, finding.observed,
                        finding.evidence_hash, finding.acceptance_rule, finding.reproducible_test,
                        finding.status, finding.resolved_at,
                    ),
                )
        return finding

    def get_findings(self, parent_qa_event_id: str) -> List[QAFinding]:
        """Get all findings for a QA event."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT * FROM uws_qa_findings WHERE parent_qa_event_id = ?",
                (parent_qa_event_id,),
            ).fetchall()
            return [self._row_to_finding(row) for row in rows]

    def resolve_finding(self, finding_id: str) -> bool:
        """Mark finding as resolved."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute(
                    "UPDATE uws_qa_findings SET status = 'RESOLVED', resolved_at = ? WHERE finding_id = ?",
                    (datetime.now(timezone.utc).isoformat(), finding_id),
                )
                return cursor.rowcount > 0

    def _row_to_finding(self, row: sqlite3.Row) -> QAFinding:
        return QAFinding(
            finding_id=row["finding_id"],
            parent_qa_event_id=row["parent_qa_event_id"],
            severity=FindingSeverity(row["severity"]),
            path=row["path"],
            expected=row["expected"],
            observed=row["observed"],
            evidence_hash=row["evidence_hash"],
            acceptance_rule=row["acceptance_rule"],
            reproducible_test=row["reproducible_test"],
            status=row["status"],
            resolved_at=row["resolved_at"],
        )

    # ========== Rework Round Operations ==========

    def insert_rework_round(self, round_record: ReworkRound) -> ReworkRound:
        """Insert rework round record."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """INSERT INTO uws_rework_rounds
                       (task_id, run_id, round_number, rework_event_id, response_type,
                        evidence_manifest_sha256, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        round_record.task_id, round_record.run_id, round_record.round_number,
                        round_record.rework_event_id, round_record.response_type.value,
                        round_record.evidence_manifest_sha256, round_record.created_at,
                    ),
                )
        return round_record

    def get_rework_rounds(self, task_id: str, run_id: str) -> List[ReworkRound]:
        """Get all rework rounds for task/run."""
        with self._lock:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT * FROM uws_rework_rounds WHERE task_id = ? AND run_id = ? ORDER BY round_number",
                (task_id, run_id),
            ).fetchall()
            return [self._row_to_rework_round(row) for row in rows]

    def get_rework_round_count(self, task_id: str, run_id: str) -> int:
        """Get count of rework rounds (for 3-round cap)."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT COUNT(*) FROM uws_rework_rounds WHERE task_id = ? AND run_id = ?",
                (task_id, run_id),
            ).fetchone()
            return row[0] if row else 0

    def _row_to_rework_round(self, row: sqlite3.Row) -> ReworkRound:
        return ReworkRound(
            task_id=row["task_id"],
            run_id=row["run_id"],
            round_number=row["round_number"],
            rework_event_id=row["rework_event_id"],
            response_type=ReworkResponseType(row["response_type"]),
            evidence_manifest_sha256=row["evidence_manifest_sha256"],
            created_at=row["created_at"],
        )

    # ========== Lease Operations ==========

    def insert_lease(self, lease: LeaseRecord) -> LeaseRecord:
        """Insert lease record."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """INSERT INTO uws_leases
                       (lease_id, task_id, run_id, holder, state, acquired_at, expires_at, heartbeat_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        lease.lease_id, lease.task_id, lease.run_id, lease.holder,
                        lease.state, lease.acquired_at, lease.expires_at, lease.heartbeat_at,
                    ),
                )
        return lease

    def update_lease(self, lease: LeaseRecord) -> bool:
        """Update lease (renewal, state change)."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute(
                    """UPDATE uws_leases SET expires_at = ?, heartbeat_at = ?, state = ?
                       WHERE lease_id = ?""",
                    (lease.expires_at, lease.heartbeat_at, lease.state, lease.lease_id),
                )
                return cursor.rowcount > 0

    def get_active_lease(self, task_id: str, run_id: str) -> Optional[LeaseRecord]:
        """Get active (non-expired) lease for task/run."""
        with self._lock:
            conn = self._get_connection()
            now = datetime.now(timezone.utc).isoformat()
            row = conn.execute(
                """SELECT * FROM uws_leases WHERE task_id = ? AND run_id = ? AND expires_at > ?
                   ORDER BY acquired_at DESC LIMIT 1""",
                (task_id, run_id, now),
            ).fetchone()
            if not row:
                return None
            return self._row_to_lease(row)

    def get_expired_leases(self) -> List[LeaseRecord]:
        """Get all expired leases."""
        with self._lock:
            conn = self._get_connection()
            now = datetime.now(timezone.utc).isoformat()
            rows = conn.execute(
                "SELECT * FROM uws_leases WHERE expires_at <= ?", (now,)
            ).fetchall()
            return [self._row_to_lease(row) for row in rows]

    def expire_lease(self, lease_id: str) -> bool:
        """Expire a lease immediately."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                cursor = conn.execute(
                    "UPDATE uws_leases SET expires_at = ? WHERE lease_id = ?",
                    (now, lease_id),
                )
                return cursor.rowcount > 0

    def _row_to_lease(self, row: sqlite3.Row) -> LeaseRecord:
        return LeaseRecord(
            lease_id=row["lease_id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            holder=row["holder"],
            state=row["state"],
            acquired_at=row["acquired_at"],
            expires_at=row["expires_at"],
            heartbeat_at=row["heartbeat_at"],
        )

    # ========== Recovery & Diagnostics ==========

    def get_event_count(self, task_id: str, run_id: str) -> int:
        """Get total event count for task/run."""
        with self._lock:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT COUNT(*) FROM uws_events WHERE task_id = ? AND run_id = ?",
                (task_id, run_id),
            ).fetchone()
            return row[0] if row else 0

    def verify_integrity(self, task_id: str, run_id: str) -> Dict[str, Any]:
        """Verify ledger integrity for a task/run."""
        with self._lock:
            conn = self._get_connection()
            events = conn.execute(
                "SELECT event_id, idempotency_key, payload_sha256, payload_json FROM uws_events WHERE task_id = ? AND run_id = ? ORDER BY timestamp_utc",
                (task_id, run_id),
            ).fetchall()

            issues = []
            seen_keys = set()
            for row in events:
                # Check idempotency key uniqueness
                if row["idempotency_key"] in seen_keys:
                    issues.append(f"Duplicate idempotency key: {row['idempotency_key']}")
                seen_keys.add(row["idempotency_key"])

                # Verify payload hash
                import hashlib
                computed = hashlib.sha256(row["payload_json"].encode()).hexdigest()
                if computed != row["payload_sha256"]:
                    issues.append(f"Payload hash mismatch for event {row['event_id']}")

            return {
                "task_id": task_id,
                "run_id": run_id,
                "event_count": len(events),
                "issues": issues,
                "valid": len(issues) == 0,
            }
