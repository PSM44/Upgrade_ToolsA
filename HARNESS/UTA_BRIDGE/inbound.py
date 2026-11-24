"""
UTA Bridge Inbound Receiver — MORCH→RORCH GitHub PR comment processor.

Implements strict parser for ----AI_START--- ... ----AI_END--- envelopes,
validates against allowlists, deduplicates via ledger, and dispatches
valid orders to RORCH work units.
"""

import os
import re
import json
import sqlite3
import subprocess
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, NamedTuple
from dataclasses import dataclass, field
from enum import Enum

# Try relative import first, fallback to absolute
try:
    from .schema import validate_receipt, compute_receipt_hash
    from .ledger import BridgeLedger, DEFAULT_LEDGER_PATH
except ImportError:
    # When running as script
    from schema import validate_receipt, compute_receipt_hash
    from ledger import BridgeLedger, DEFAULT_LEDGER_PATH


# ─── Constants ──────────────────────────────────────────────────────────────
PROTOCOL_VERSIONS = {"UTA_BRIDGE_TRANSITION_V0", "UTA_BRIDGE_V1"}
ALLOWED_SENDERS = {"MORCH", "RORCH", "HUMAN"}
ALLOWED_RECIPIENTS = {"MORCH", "RORCH", "HUMAN"}
ALLOWED_MODES = {
    "READ_ONLY_DESIGN", "READ_ONLY_PREFLIGHT", "DESIGN_CORRECTION",
    "APPROVAL_REQUEST", "APPROVAL_GRANTED", "REJECTED", "EXECUTE",
    "REVIEW_FEEDBACK_NO_EXECUTION", "AUTHORIZED_LIMITED_IMPLEMENTATION",
    "HUMAN_AUTHORIZED_GROUPED_INTEGRATION", "INTEGRATION_RECEIPT",
    "DESIGN_READY_FOR_REVIEW", "CORRECTED_NOT_ACCEPTED", "IMPLEMENTED_NOT_ACCEPTED"
}
ALLOWED_STATUSES = {
    "REQUESTED_NOT_ACCEPTED", "DESIGN_READY_FOR_REVIEW", "BLOCKED_WITH_EVIDENCE",
    "ACCEPTED", "REJECTED", "EXECUTED_NOT_ACCEPTED", "FAILED",
    "PREFLIGHT_PARTIAL_IMPLEMENTATION_NOT_AUTHORIZED",
    "PREFLIGHT_ONLY_NOT_ACCEPTED", "IMPLEMENTED_NOT_ACCEPTED", "CORRECTED_NOT_ACCEPTED"
}
ALLOWED_AUTHOR = "PSM44"
ALLOWED_REPO = "PSM44/Upgrade_ToolsA"
ALLOWED_PR = 2  # PR #2

# Delimiters
START_DELIM = "----AI_START---"
END_DELIM = "----AI_END----"

# State directory
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "uta-bridge"
STATE_DIR.mkdir(parents=True, exist_ok=True)
INBOUND_LEDGER_PATH = STATE_DIR / "inbound_ledger.db"


# ─── Data Classes ───────────────────────────────────────────────────────────
@dataclass
class ParsedEnvelope:
    """Parsed and validated MORCH order envelope."""
    protocol: str
    message_id: str
    correlation_id: str
    sender: str
    recipient: str
    task_id: str
    mode: str
    status: str
    reference_pr: str
    base_head_observed: str
    payload: Dict[str, Any]
    auth: Dict[str, Any]
    ack_required: bool = True
    raw_body: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "protocol": self.protocol,
            "message_id": self.message_id,
            "correlation_id": self.correlation_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "task_id": self.task_id,
            "mode": self.mode,
            "status": self.status,
            "reference_pr": self.reference_pr,
            "base_head_observed": self.base_head_observed,
            "payload": self.payload,
            "auth": self.auth,
            "ack_required": self.ack_required,
        }


class ValidationError(Exception):
    """Envelope validation error."""
    pass


# ─── Envelope Parser ────────────────────────────────────────────────────────
class EnvelopeParser:
    """Strict parser for ----AI_START--- ... ----AI_END--- envelopes."""

    FIELD_PATTERNS = {
        "protocol": re.compile(r"PROTOCOL:\s*(\S+)"),
        "message_id": re.compile(r"MESSAGE_ID:\s*(\S+)"),
        "correlation_id": re.compile(r"CORRELATION_ID:\s*(\S+)"),
        "sender": re.compile(r"SENDER:\s*(\S+)"),
        "recipient": re.compile(r"RECIPIENT:\s*(\S+)"),
        "task_id": re.compile(r"TASK_ID:\s*(\S+)"),
        "mode": re.compile(r"MODE:\s*(\S+)"),
        "status": re.compile(r"STATUS:\s*(\S+)"),
        "reference_pr": re.compile(r"REFERENCE_PR:\s*(#?\d+)"),
        "base_head_observed": re.compile(r"BASE_HEAD_OBSERVED:\s*(\S+)"),
        "authority": re.compile(r"AUTHORITY:\s*(\S+)"),
        "ack_required": re.compile(r"ACK_REQUIRED:\s*(\S+)"),
    }

    REQUIRED_FIELDS = {
        "protocol", "message_id", "correlation_id", "sender", "recipient",
        "task_id", "mode", "status", "reference_pr"
    }
    OPTIONAL_FIELDS = {"authority", "ack_required"}

    def parse(self, body: str) -> ParsedEnvelope:
        """Parse and validate envelope from comment body."""
        # Check exact delimiters
        if not body.startswith(START_DELIM):
            raise ValidationError(f"Missing start delimiter: {START_DELIM}")
        if not body.endswith(END_DELIM):
            raise ValidationError(f"Missing end delimiter: {END_DELIM}")

        # Extract content between delimiters
        content = body[len(START_DELIM):-len(END_DELIM)].strip()

        # Extract fields
        fields = {}
        for key, pattern in self.FIELD_PATTERNS.items():
            match = pattern.search(content)
            if match:
                fields[key] = match.group(1).strip()
            elif key in self.REQUIRED_FIELDS:
                raise ValidationError(f"Missing required field: {key}")
            else:
                fields[key] = ""

        # Extract payload JSON (between PAYLOAD: and next field or end)
        payload = {}
        payload_match = re.search(r"PAYLOAD:\s*(\{.*?\})(?:\n\w+:|$)", content, re.DOTALL)
        if payload_match:
            try:
                payload = json.loads(payload_match.group(1))
            except json.JSONDecodeError as e:
                raise ValidationError(f"Invalid PAYLOAD JSON: {e}")

        # Validate protocol
        if fields["protocol"] not in PROTOCOL_VERSIONS:
            raise ValidationError(f"Unsupported protocol: {fields['protocol']}")

        # Validate sender/recipient
        if fields["sender"] not in ALLOWED_SENDERS:
            raise ValidationError(f"Invalid sender: {fields['sender']}")
        if fields["recipient"] not in ALLOWED_RECIPIENTS:
            raise ValidationError(f"Invalid recipient: {fields['recipient']}")

        # Validate mode/status
        if fields["mode"] not in ALLOWED_MODES:
            raise ValidationError(f"Invalid mode: {fields['mode']}")
        if fields["status"] not in ALLOWED_STATUSES:
            raise ValidationError(f"Invalid status: {fields['status']}")

        # Validate reference PR format
        ref_pr = fields["reference_pr"].lstrip("#")
        if not ref_pr.isdigit():
            raise ValidationError(f"Invalid REFERENCE_PR: {fields['reference_pr']}")

        # Construct auth dict
        auth = {
            "github_author": ALLOWED_AUTHOR,  # Will be overridden by scanner
            "github_repo": ALLOWED_REPO,
            "github_comment_id": 0,  # Will be overridden by scanner
            "retrieved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "verified_by": "RORCH",
        }

        ack_required = fields.get("ack_required", "true").lower() == "true"

        return ParsedEnvelope(
            protocol=fields["protocol"],
            message_id=fields["message_id"],
            correlation_id=fields["correlation_id"],
            sender=fields["sender"],
            recipient=fields["recipient"],
            task_id=fields["task_id"],
            mode=fields["mode"],
            status=fields["status"],
            reference_pr=f"#{ref_pr}",
            base_head_observed=fields["base_head_observed"],
            payload=payload,
            auth=auth,
            ack_required=ack_required,
        )


# ─── Inbound Ledger ─────────────────────────────────────────────────────────
class InboundLedger:
    """Deduplication ledger for inbound MORCH orders."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(INBOUND_LEDGER_PATH)
        self._init_db()

    def _init_db(self):
        """Initialize the inbound ledger schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inbound_orders (
                    message_id       TEXT PRIMARY KEY,
                    correlation_id   TEXT NOT NULL,
                    sender           TEXT NOT NULL,
                    recipient        TEXT NOT NULL,
                    task_id          TEXT NOT NULL,
                    mode             TEXT NOT NULL,
                    status           TEXT NOT NULL,
                    reference_pr     TEXT NOT NULL,
                    base_head_observed TEXT NOT NULL,
                    github_comment_id INTEGER NOT NULL UNIQUE,
                    github_author     TEXT NOT NULL,
                    github_repo       TEXT NOT NULL,
                    github_html_url   TEXT NOT NULL,
                    retrieved_at      TEXT NOT NULL,
                    payload_hash      TEXT NOT NULL,
                    envelope_hash     TEXT NOT NULL,
                    state            TEXT NOT NULL DEFAULT 'NEW'
                        CHECK(state IN ('NEW','VALIDATED','DISPATCHED','EXECUTING',
                                        'EVIDENCE_WRITTEN','RESPONSE_POSTED','ACKNOWLEDGED',
                                        'FAILED','REJECTED','BLOCKED','DUPLICATE','CONFLICT')),
                    retry_count      INTEGER NOT NULL DEFAULT 0,
                    max_retries      INTEGER NOT NULL DEFAULT 3,
                    last_attempt_at  TEXT,
                    error_detail     TEXT,
                    response_comment_id INTEGER,
                    response_posted_at  TEXT,
                    ack_received_at     TEXT,
                    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_inbound_correlation
                ON inbound_orders(correlation_id);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_inbound_state
                ON inbound_orders(state);
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_inbound_github_comment
                ON inbound_orders(github_comment_id);
            """)

    def _row_to_dict(self, cursor: sqlite3.Cursor, row: Tuple) -> Dict[str, Any]:
        columns = [d[0] for d in cursor.description]
        return dict(zip(columns, row))

    def _stable_envelope_hash(self, envelope: ParsedEnvelope) -> str:
        """Compute stable hash from envelope fields that don't change between parses."""
        stable_dict = {
            "protocol": envelope.protocol,
            "message_id": envelope.message_id,
            "correlation_id": envelope.correlation_id,
            "sender": envelope.sender,
            "recipient": envelope.recipient,
            "task_id": envelope.task_id,
            "mode": envelope.mode,
            "status": envelope.status,
            "reference_pr": envelope.reference_pr,
            "base_head_observed": envelope.base_head_observed or "",
            "payload": envelope.payload,
            "ack_required": envelope.ack_required,
        }
        canonical = json.dumps(stable_dict, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def claim_order(self, envelope: ParsedEnvelope, github_comment_id: int,
                    github_author: str, github_repo: str, github_html_url: str) -> Tuple[str, str]:
        """
        Atomically claim an inbound order.

        Returns: (state, detail) where state is one of:
        - CLAIMED: newly validated and claimed
        - DUPLICATE: already processed (ACKNOWLEDGED/RESPONSE_POSTED)
        - CONFLICT: same message_id different payload
        - REJECTED: validation failed
        """
        # Compute stable hashes
        envelope_hash = self._stable_envelope_hash(envelope)
        payload_canonical = json.dumps(envelope.payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload_canonical.encode()).hexdigest()

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        message_id = envelope.message_id
        correlation_id = envelope.correlation_id

        with sqlite3.connect(self.db_path, timeout=30.0) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")

            # Check existing
            cursor = conn.execute(
                "SELECT state, envelope_hash FROM inbound_orders WHERE message_id = ?",
                (message_id,)
            )
            existing = cursor.fetchone()

            if existing:
                existing_state, existing_hash = existing
                if existing_hash != envelope_hash:
                    return "CONFLICT", f"conflict: same message_id different payload (existing: {existing_hash[:16]}..., new: {envelope_hash[:16]}...)"
                # True duplicate: same message_id, same payload
                # ACKNOWLEDGED/RESPONSE_POSTED are truly done; DISPATCHED is in progress
                if existing_state in ("ACKNOWLEDGED", "RESPONSE_POSTED", "DISPATCHED"):
                    return "DUPLICATE", f"duplicate: already processed (state: {existing_state})"
                # VALIDATED = validated but not yet dispatched; re-claim for dispatch
                if existing_state == "VALIDATED":
                    conn.execute("""
                        UPDATE inbound_orders SET
                            state = 'VALIDATED',
                            retry_count = retry_count + 1,
                            last_attempt_at = ?,
                            updated_at = ?
                        WHERE message_id = ?
                    """, (now, now, message_id))
                    return "CLAIMED", "Re-claimed for dispatch"
                # Allow retry for failed/blocked/rejected states
                if existing_state in ("FAILED", "BLOCKED", "REJECTED", "NEW"):
                    conn.execute("""
                        UPDATE inbound_orders SET
                            state = 'VALIDATED',
                            retry_count = retry_count + 1,
                            last_attempt_at = ?,
                            updated_at = ?
                        WHERE message_id = ? AND state NOT IN ('ACKNOWLEDGED', 'RESPONSE_POSTED')
                    """, (now, now, message_id))
                    return "CLAIMED", "Retry claimed"
                # Other states (should not happen) - treat as duplicate
                return "DUPLICATE", f"duplicate: already exists (state: {existing_state})"

            # Insert new
            try:
                conn.execute("""
                    INSERT INTO inbound_orders (
                        message_id, correlation_id, sender, recipient, task_id, mode,
                        status, reference_pr, base_head_observed, github_comment_id,
                        github_author, github_repo, github_html_url, retrieved_at,
                        payload_hash, envelope_hash, state, retry_count, max_retries,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'VALIDATED', 0, 3, ?, ?)
                """, (
                    envelope.message_id,
                    envelope.correlation_id,
                    envelope.sender,
                    envelope.recipient,
                    envelope.task_id,
                    envelope.mode,
                    envelope.status,
                    envelope.reference_pr,
                    envelope.base_head_observed,
                    github_comment_id,  # Use the actual GitHub comment ID
                    github_author,
                    github_repo,
                    github_html_url,
                    now,
                    payload_hash,
                    envelope_hash,
                    now,
                    now,
                ))
                return "CLAIMED", "New order validated and claimed"
            except sqlite3.IntegrityError as e:
                if "UNIQUE constraint failed: inbound_orders.github_comment_id" in str(e):
                    return "CONFLICT", "GitHub comment ID already associated with different message"
                raise

    def get_order(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Get order by message_id."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT * FROM inbound_orders WHERE message_id = ?",
                (message_id,)
            )
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(cursor, row)
            return None

    def _row_to_dict(self, cursor: sqlite3.Cursor, row: Tuple) -> Dict[str, Any]:
        columns = [d[0] for d in cursor.description]
        return dict(zip(columns, row))

    def update_state(self, message_id: str, state: str, **kwargs) -> bool:
        """Update order state and optional fields."""
        allowed_fields = {
            "state", "github_comment_id", "github_author", "github_repo",
            "github_html_url", "retrieved_at", "retry_count", "last_attempt_at",
            "error_detail", "response_comment_id", "response_posted_at",
            "ack_received_at"
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        updates["state"] = state
        updates["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not updates:
            return False

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [message_id]

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                f"UPDATE inbound_orders SET {set_clause} WHERE message_id = ?",
                values,
            )
            return cursor.rowcount > 0

    def get_pending_orders(self, max_retries: int = 3) -> List[Dict[str, Any]]:
        """Get orders ready for dispatch."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT * FROM inbound_orders
                WHERE state IN ('VALIDATED', 'DISPATCHED')
                AND retry_count < ?
                ORDER BY created_at ASC
            """, (max_retries,))
            return [self._row_to_dict(cursor, row) for row in cursor.fetchall()]


# ─── GitHub Scanner ─────────────────────────────────────────────────────────
class GitHubScanner:
    """Scans GitHub PR comments for MORCH orders."""

    def __init__(self, repo: str = ALLOWED_REPO, pr_number: int = ALLOWED_PR,
                 gh_cli: str = "gh", ledger: InboundLedger = None):
        self.repo = repo
        self.pr_number = pr_number
        self.gh_cli = gh_cli
        self.ledger = ledger or InboundLedger()
        self.parser = EnvelopeParser()

    def _check_gh_auth(self) -> bool:
        try:
            result = subprocess.run(
                [self.gh_cli, "auth", "status"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    def scan(self) -> List[Dict[str, Any]]:
        """Scan PR comments for valid MORCH orders."""
        if not self._check_gh_auth():
            raise RuntimeError("GitHub CLI not authenticated")

        try:
            result = subprocess.run(
                [self.gh_cli, "api", f"/repos/{self.repo}/issues/{self.pr_number}/comments"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to fetch comments: {result.stderr}")

            comments = json.loads(result.stdout)
            results = []

            for comment in comments:
                body = comment.get("body", "")
                if not body.startswith(START_DELIM):
                    continue

                # Parse envelope
                try:
                    envelope = self.parser.parse(body)
                except ValidationError as e:
                    continue  # Skip malformed

                # Validate author/repo (repo is known from the PR we're scanning)
                author = comment.get("user", {}).get("login", "")
                if author != ALLOWED_AUTHOR:
                    continue
                repo = ALLOWED_REPO  # We know we're scanning this repo's PR

                # Validate recipient
                if envelope.recipient != "RORCH":
                    continue

                # Enrich with GitHub metadata
                envelope.auth["github_author"] = author
                envelope.auth["github_repo"] = repo
                envelope.auth["github_comment_id"] = comment["id"]
                envelope.auth["github_html_url"] = comment["html_url"]
                envelope.auth["retrieved_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

                # Claim in ledger
                state, detail = self.ledger.claim_order(
                    envelope,
                    comment["id"],
                    author,
                    repo,
                    comment["html_url"]
                )

                result = {
                    "envelope": envelope.to_dict(),
                    "ledger_state": state,
                    "detail": detail,
                    "github_comment_id": comment["id"],
                    "github_html_url": comment["html_url"],
                }
                results.append(result)

                # Update ledger with GitHub metadata
                # Map claim states to ledger states
                ledger_state_map = {
                    "CLAIMED": "VALIDATED",
                    "DUPLICATE": "DUPLICATE",
                    "CONFLICT": "CONFLICT",
                }
                ledger_state = ledger_state_map.get(state, state)
                if state in ("CLAIMED", "DUPLICATE", "CONFLICT"):
                    self.ledger.update_state(
                        envelope.message_id,
                        ledger_state,
                        github_comment_id=comment["id"],
                        github_author=author,
                        github_repo=repo,
                        github_html_url=comment["html_url"],
                        retrieved_at=envelope.auth["retrieved_at"],
                    )

            return results

        except Exception as e:
            raise RuntimeError(f"Scanner error: {e}")


# ─── Dispatcher ─────────────────────────────────────────────────────────────
class Dispatcher:
    """Dispatches validated orders to RORCH work units."""

    def __init__(self, ledger: InboundLedger = None):
        self.ledger = ledger or InboundLedger()

    def dispatch(self, order: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Dispatch a validated order to RORCH execution.

        Returns (success, result_dict).
        """
        envelope = order.get("envelope", {})
        message_id = envelope.get("message_id")
        mode = envelope.get("mode")
        task_id = envelope.get("task_id")

        # Check if EXECUTE mode requires approval
        if mode == "EXECUTE":
            # Check for APPROVAL_GRANTED in ledger for same correlation
            with sqlite3.connect(self.ledger.db_path) as conn:
                cursor = conn.execute("""
                    SELECT 1 FROM inbound_orders
                    WHERE correlation_id = ? AND mode = 'APPROVAL_GRANTED' AND state = 'ACKNOWLEDGED'
                """, (envelope.get("correlation_id"),))
                if not cursor.fetchone():
                    self.ledger.update_state(
                        message_id, "BLOCKED",
                        error_detail="EXECUTE mode requires prior APPROVAL_GRANTED with ACKNOWLEDGED state"
                    )
                    return False, {"blocked": True, "reason": "Missing APPROVAL_GRANTED"}

        # Update state to DISPATCHED
        self.ledger.update_state(message_id, "DISPATCHED")

        # For now, return the order for RORCH to process
        # Actual DSH dispatch would happen here
        return True, {
            "dispatched": True,
            "message_id": message_id,
            "task_id": task_id,
            "mode": mode,
            "note": "Order dispatched for RORCH processing. DSH dispatch interface not yet implemented."
        }


# ─── Main Inbound Processor ─────────────────────────────────────────────────
def process_inbound(
    repo: str = ALLOWED_REPO,
    pr_number: int = ALLOWED_PR,
    gh_cli: str = "gh",
) -> Dict[str, Any]:
    """
    Main entry point: scan → validate → deduplicate → dispatch.

    Returns summary of processed orders.
    """
    ledger = InboundLedger()
    scanner = GitHubScanner(repo, pr_number, gh_cli, ledger)
    dispatcher = Dispatcher(ledger)

    # Scan
    results = scanner.scan()

    # Dispatch pending orders
    dispatched = 0
    for result in results:
        if result["ledger_state"] == "CLAIMED":
            success, detail = dispatcher.dispatch(result)
            if success:
                dispatched += 1

    return {
        "scanned": len(results),
        "claimed": sum(1 for r in results if r["ledger_state"] == "CLAIMED"),
        "duplicates": sum(1 for r in results if r["ledger_state"] == "DUPLICATE"),
        "conflicts": sum(1 for r in results if r["ledger_state"] == "CONFLICT"),
        "dispatched": dispatched,
        "results": results,
    }


# ─── CLI Entry Point ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="UTA Bridge Inbound Processor")
    parser.add_argument("--repo", default=ALLOWED_REPO)
    parser.add_argument("--pr", type=int, default=ALLOWED_PR)
    parser.add_argument("--gh-cli", default="gh")
    parser.add_argument("--scan-only", action="store_true", help="Only scan, don't dispatch")
    args = parser.parse_args()

    if args.scan_only:
        ledger = InboundLedger()
        scanner = GitHubScanner(args.repo, args.pr, args.gh_cli, ledger)
        results = scanner.scan()
        print(json.dumps(results, indent=2))
    else:
        result = process_inbound(args.repo, args.pr, args.gh_cli)
        print(json.dumps(result, indent=2))