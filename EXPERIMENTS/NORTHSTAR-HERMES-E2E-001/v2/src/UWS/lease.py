"""
Lease management for crash recovery in UWS.
Implements lease acquisition, renewal, expiration, and recovery scanning.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from .models import LeaseRecord
from .idempotency import generate_lease_id


DEFAULT_LEASE_TTL_SECONDS = 300  # 5 minutes
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 60  # 1 minute


@dataclass
class LeaseConfig:
    ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS
    heartbeat_interval_seconds: int = DEFAULT_HEARTBEAT_INTERVAL_SECONDS


class LeaseManager:
    """Manages leases for crash recovery and concurrent execution prevention."""

    def __init__(self, db_path: str, config: Optional[LeaseConfig] = None):
        self.db_path = db_path
        self.config = config or LeaseConfig()
        self._lock = threading.Lock()
        self._current_lease: Optional[LeaseRecord] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with proper settings."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def acquire_lease(
        self,
        task_id: str,
        run_id: str,
        holder: str,
        state: str,
        ttl_seconds: Optional[int] = None,
    ) -> Optional[LeaseRecord]:
        """
        Acquire a lease for the given task/run.
        Returns LeaseRecord if successful, None if lease already held by another.
        """
        ttl = ttl_seconds or self.config.ttl_seconds
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl)
        lease_id = generate_lease_id()

        with self._get_connection() as conn:
            with conn:
                # Check for existing active lease
                existing = conn.execute(
                    """SELECT lease_id, holder, state, expires_at FROM uws_leases
                       WHERE task_id = ? AND run_id = ? AND expires_at > ?""",
                    (task_id, run_id, now.isoformat()),
                ).fetchone()

                if existing:
                    existing_lease_id, existing_holder, existing_state, existing_expires = existing
                    if existing_holder != holder:
                        return None  # Another holder has active lease
                    # Same holder - renew existing lease
                    conn.execute(
                        """UPDATE uws_leases SET expires_at = ?, heartbeat_at = ?, state = ?
                           WHERE lease_id = ?""",
                        (expires_at.isoformat(), now.isoformat(), state, existing_lease_id),
                    )
                    lease = LeaseRecord(
                        lease_id=existing_lease_id,
                        task_id=task_id,
                        run_id=run_id,
                        holder=holder,
                        state=state,
                        acquired_at=conn.execute(
                            "SELECT acquired_at FROM uws_leases WHERE lease_id = ?",
                            (existing_lease_id,)
                        ).fetchone()[0],
                        expires_at=expires_at.isoformat(),
                        heartbeat_at=now.isoformat(),
                    )
                else:
                    # Create new lease
                    acquired_at = now.isoformat()
                    conn.execute(
                        """INSERT INTO uws_leases
                           (lease_id, task_id, run_id, holder, state, acquired_at, expires_at, heartbeat_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (lease_id, task_id, run_id, holder, state, acquired_at,
                         expires_at.isoformat(), now.isoformat()),
                    )
                    lease = LeaseRecord(
                        lease_id=lease_id,
                        task_id=task_id,
                        run_id=run_id,
                        holder=holder,
                        state=state,
                        acquired_at=acquired_at,
                        expires_at=expires_at.isoformat(),
                        heartbeat_at=now.isoformat(),
                    )

        self._current_lease = lease
        self._start_heartbeat(lease, state)
        return lease

    def _start_heartbeat(self, lease: LeaseRecord, state: str) -> None:
        """Start background heartbeat thread for lease renewal."""
        self._stop_heartbeat.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(lease.lease_id, state),
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self, lease_id: str, state: str) -> None:
        """Background thread to renew lease periodically."""
        while not self._stop_heartbeat.wait(self.config.heartbeat_interval_seconds):
            self.renew_lease(lease_id, state)

    def renew_lease(self, lease_id: str, state: str, ttl_seconds: Optional[int] = None) -> bool:
        """Renew lease with new expiration time."""
        ttl = ttl_seconds or self.config.ttl_seconds
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl)

        with self._get_connection() as conn:
            with conn:
                cursor = conn.execute(
                    """UPDATE uws_leases SET expires_at = ?, heartbeat_at = ?, state = ?
                       WHERE lease_id = ? AND expires_at > ?""",
                    (expires_at.isoformat(), now.isoformat(), state, lease_id, now.isoformat()),
                )
                if cursor.rowcount > 0:
                    if self._current_lease and self._current_lease.lease_id == lease_id:
                        self._current_lease.expires_at = expires_at.isoformat()
                        self._current_lease.heartbeat_at = now.isoformat()
                        self._current_lease.state = state
                    return True
        return False

    def release_lease(self, lease_id: str) -> bool:
        """Release a lease (mark as expired)."""
        self._stop_heartbeat.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5.0)

        now = datetime.now(timezone.utc)
        with self._get_connection() as conn:
            with conn:
                cursor = conn.execute(
                    "UPDATE uws_leases SET expires_at = ? WHERE lease_id = ?",
                    (now.isoformat(), lease_id),
                )
                if self._current_lease and self._current_lease.lease_id == lease_id:
                    self._current_lease = None
                return cursor.rowcount > 0

    def get_lease(self, task_id: str, run_id: str) -> Optional[LeaseRecord]:
        """Get current active lease for task/run."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            row = conn.execute(
                """SELECT lease_id, task_id, run_id, holder, state, acquired_at, expires_at, heartbeat_at
                   FROM uws_leases WHERE task_id = ? AND run_id = ? AND expires_at > ?
                   ORDER BY acquired_at DESC LIMIT 1""",
                (task_id, run_id, now),
            ).fetchone()
            if row:
                return LeaseRecord(
                    lease_id=row[0], task_id=row[1], run_id=row[2],
                    holder=row[3], state=row[4], acquired_at=row[5],
                    expires_at=row[6], heartbeat_at=row[7],
                )
        return None

    def scan_expired_leases(self) -> list[LeaseRecord]:
        """Scan for expired leases that need recovery."""
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT lease_id, task_id, run_id, holder, state, acquired_at, expires_at, heartbeat_at
                   FROM uws_leases WHERE expires_at <= ?""",
                (now,),
            ).fetchall()
            return [
                LeaseRecord(
                    lease_id=r[0], task_id=r[1], run_id=r[2],
                    holder=r[3], state=r[4], acquired_at=r[5],
                    expires_at=r[6], heartbeat_at=r[7],
                )
                for r in rows
            ]

    def recover_from_lease(self, lease: LeaseRecord) -> dict:
        """
        Recover state from an expired lease.
        Returns dict with recovery info: last_state, last_event_id, needs_recovery.
        """
        # Get the last event for this task/run
        with self._get_connection() as conn:
            last_event = conn.execute(
                """SELECT event_id, event_type, payload_json FROM uws_events
                   WHERE task_id = ? AND run_id = ?
                   ORDER BY timestamp_utc DESC LIMIT 1""",
                (lease.task_id, lease.run_id),
            ).fetchone()

        if not last_event:
            return {"needs_recovery": False, "reason": "no_events"}

        event_id, event_type, payload_json = last_event

        # Determine if recovery needed based on state
        terminal_states = {
            "ACCEPTED", "TERMINAL_COMPLETED", "TERMINAL_BLOCKED", "TERMINAL_FAILED"
        }
        if event_type.value if hasattr(event_type, 'value') else event_type in terminal_states:
            return {"needs_recovery": False, "reason": "terminal_state_reached"}

        # State was interrupted - needs recovery
        return {
            "needs_recovery": True,
            "last_event_id": event_id,
            "last_event_type": event_type,
            "last_state": lease.state,
            "lease_holder": lease.holder,
        }

    def get_current_lease(self) -> Optional[LeaseRecord]:
        """Get the currently held lease."""
        return self._current_lease


from contextlib import contextmanager
from typing import Generator


@contextmanager
def lease_context(
    lease_manager: LeaseManager,
    task_id: str,
    run_id: str,
    holder: str,
    initial_state: str,
) -> Generator[LeaseRecord, None, None]:
    """Context manager for lease acquisition and automatic release."""
    lease = lease_manager.acquire_lease(task_id, run_id, holder, initial_state)
    if not lease:
        raise RuntimeError(f"Could not acquire lease for {task_id}/{run_id} - held by another process")
    try:
        yield lease
    finally:
        lease_manager.release_lease(lease.lease_id)
