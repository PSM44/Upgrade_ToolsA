"""
Bridge ledger — transactional persistence for UTA Bridge.

Uses the existing SQLite schema at ~/.hermes/uta_bridge/ledger.db
with the bridge_messages table.
"""

import sqlite3
import os
import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple
from contextlib import contextmanager

from .schema import validate_receipt, canonicalize_receipt, compute_receipt_hash


DEFAULT_LEDGER_PATH = os.path.expanduser("~/.hermes/uta_bridge/ledger.db")


def _map_producer_to_sender(producer: str) -> str:
    """Map producer to valid sender value."""
    if "RORCH" in producer:
        return "RORCH"
    if "MORCH" in producer:
        return "MORCH"
    return "HUMAN"


def _map_consumer_to_recipient(consumer: str) -> str:
    """Map consumer to valid recipient value."""
    if "RORCH" in consumer:
        return "RORCH"
    if "MORCH" in consumer:
        return "MORCH"
    return "HUMAN"


def _map_state(state: str) -> str:
    """Map receipt state to valid ledger state."""
    valid_states = {
        'NEW', 'VALIDATED', 'DISPATCHED', 'EXECUTING',
        'EVIDENCE_WRITTEN', 'RESPONSE_POSTED', 'ACKNOWLEDGED', 'FAILED', 'REJECTED'
    }
    if state in valid_states:
        return state
    mapping = {
        'EMITTED': 'NEW',
        'PERSISTED': 'VALIDATED',
        'DELIVERED': 'DISPATCHED',
        'ACK_RECEIVED': 'ACKNOWLEDGED',
        'MORCH_REVIEW': 'EVIDENCE_WRITTEN',
        'ACCEPTED': 'ACKNOWLEDGED',
        'REWORK_REQUIRED': 'REJECTED',
        'BLOCKED': 'FAILED',
    }
    return mapping.get(state, 'NEW')


def _map_state_for_update(state: str) -> str:
    """Map update state to valid ledger state (for mark_delivered, mark_ack, mark_blocked)."""
    mapping = {
        'DELIVERED': 'DISPATCHED',
        'ACK_RECEIVED': 'ACKNOWLEDGED',
        'BLOCKED': 'FAILED',
    }
    return mapping.get(state, state)


class BridgeLedger:
    """
    Transactional ledger for bridge messages.

    Provides atomic write/read operations with proper locking.
    """

    def __init__(self, db_path: str = DEFAULT_LEDGER_PATH):
        self.db_path = db_path
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Ledger not found at {db_path}")

    @contextmanager
    def _transaction(self):
        """Context manager for database transactions."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _row_to_dict(self, cursor: sqlite3.Cursor, row: Tuple) -> Dict[str, Any]:
        """Convert sqlite row to dict with column names."""
        columns = [d[0] for d in cursor.description]
        return dict(zip(columns, row))

    def store_outbox(self, receipt: Any) -> str:
        """
        Store a receipt in the outbox (NEW state).

        Returns the message_id (receipt_id) of the stored record.
        """
        # Convert Receipt dataclass to dict if needed
        if hasattr(receipt, 'to_dict'):
            receipt = receipt.to_dict()

        # Validate receipt first
        result = validate_receipt(receipt)
        if not result.valid:
            raise ValueError(f"Invalid receipt: {result.errors}")

        # Store the provided artifact_sha256 (hash of the delivered artifact)
        # Note: this is distinct from the receipt's own canonical hash
        message_id = receipt["receipt_id"]
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload_hash = receipt.get("artifact_sha256", "")

        sender = _map_producer_to_sender(receipt.get("producer", ""))
        recipient = _map_consumer_to_recipient(receipt.get("consumer", ""))
        ledger_state = _map_state(receipt.get("state", "NEW"))

        # Use negative unique value for github_comment_id placeholder to avoid UNIQUE conflict
        placeholder_comment_id = -int(message_id.split('-')[-1][:8], 16)

        with self._transaction() as conn:
            # Check for idempotency key conflict
            cursor = conn.execute(
                "SELECT message_id FROM bridge_messages WHERE correlation_id = ?",
                (receipt.get("idempotency_key"),),
            )
            existing = cursor.fetchone()
            if existing:
                # Idempotent: return existing message_id
                return existing[0]

            conn.execute(
                """
                INSERT INTO bridge_messages (
                    message_id, correlation_id, sender, recipient, task_id, mode,
                    status, reference_pr, base_head_observed, github_comment_id,
                    github_author, github_repo, github_html_url, retrieved_at,
                    state, retry_count, max_retries, last_attempt_at, error_detail,
                    response_comment_id, response_posted_at, ack_received_at,
                    payload_sha256, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    receipt.get("idempotency_key"),
                    sender,
                    recipient,
                    receipt.get("task_id"),
                    "RECEIPT",  # mode
                    ledger_state,  # status mapped to ledger state
                    receipt.get("reference_pr", ""),  # reference_pr (required)
                    None,  # base_head_observed
                    placeholder_comment_id,  # github_comment_id (NOT NULL UNIQUE)
                    "",  # github_author (NOT NULL)
                    "",  # github_repo (NOT NULL)
                    "",  # github_html_url (NOT NULL)
                    now,  # retrieved_at (NOT NULL, use creation time as placeholder)
                    ledger_state,  # state
                    0,  # retry_count
                    3,  # max_retries
                    None,  # last_attempt_at
                    None,  # error_detail
                    None,  # response_comment_id
                    None,  # response_posted_at
                    None,  # ack_received_at
                    payload_hash,
                    now,
                    now,
                ),
            )

        return message_id

    def get_by_id(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a message by message_id."""
        with self._transaction() as conn:
            cursor = conn.execute(
                "SELECT * FROM bridge_messages WHERE message_id = ?",
                (message_id,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(cursor, row)
            return None

    def get_by_idempotency_key(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve a message by idempotency key (correlation_id)."""
        with self._transaction() as conn:
            cursor = conn.execute(
                "SELECT * FROM bridge_messages WHERE correlation_id = ?",
                (idempotency_key,),
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(cursor, row)
            return None

    def update_state(self, message_id: str, state: str, **kwargs) -> bool:
        """
        Update message state and optional fields.

        Returns True if updated, False if not found.
        """
        allowed_fields = {
            "state", "status", "github_comment_id", "github_author",
            "github_repo", "github_html_url", "retrieved_at",
            "retry_count", "last_attempt_at", "error_detail",
            "response_comment_id", "response_posted_at", "ack_received_at",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        # Map update state to valid ledger state
        updates["state"] = _map_state_for_update(state)
        updates["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not updates:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [message_id]

        with self._transaction() as conn:
            cursor = conn.execute(
                f"UPDATE bridge_messages SET {set_clause} WHERE message_id = ?",
                values,
            )
            return cursor.rowcount > 0

    def mark_delivered(self, message_id: str, github_comment_id: int,
                       github_author: str, github_repo: str, github_html_url: str) -> bool:
        """Mark a message as delivered with GitHub comment metadata."""
        return self.update_state(
            message_id,
            "DELIVERED",
            github_comment_id=github_comment_id,
            github_author=github_author,
            github_repo=github_repo,
            github_html_url=github_html_url,
            retrieved_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def mark_ack_received(self, message_id: str, ack_comment_id: int) -> bool:
        """Mark ACK as received from consumer."""
        return self.update_state(
            message_id,
            "ACK_RECEIVED",
            response_comment_id=ack_comment_id,
            ack_received_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def mark_blocked(self, message_id: str, error: str) -> bool:
        """Mark message as blocked with error."""
        return self.update_state(
            message_id,
            "BLOCKED",
            error_detail=error,
        )

    def get_pending_outbox(self, max_retries: int = 3) -> List[Dict[str, Any]]:
        """Get messages ready for delivery (NEW state, retries < max)."""
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM bridge_messages
                WHERE state = 'NEW' AND retry_count < ?
                ORDER BY created_at ASC
                """,
                (max_retries,),
            )
            return [self._row_to_dict(cursor, row) for row in cursor.fetchall()]

    def count_by_state(self, state: str) -> int:
        """Count messages by state."""
        with self._transaction() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM bridge_messages WHERE state = ?",
                (state,),
            )
            return cursor.fetchone()[0]

    def verify_persistence(self, message_id: str, expected_hash: str) -> bool:
        """Verify a message was persisted with correct hash."""
        msg = self.get_by_id(message_id)
        if not msg:
            return False
        return msg.get("payload_sha256") == expected_hash