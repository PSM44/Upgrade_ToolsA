"""Durable, authenticated SQLite transport for the NorthStar fixture.

The transport owns message state. Payload fields never grant authority: actor
identity is verified with an out-of-band actor registry and HMAC capability.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


class TransportError(RuntimeError):
    pass


class AuthenticationError(TransportError):
    pass


class AuthorizationError(TransportError):
    pass


class ReplayError(TransportError):
    pass


class ConflictError(TransportError):
    pass


class LeaseError(TransportError):
    pass


@dataclass(frozen=True)
class Delivery:
    message_id: str
    producer: str
    recipient: str
    message_type: str
    payload: dict[str, Any]
    payload_sha256: str
    claim_token: str
    lease_expires_at: float
    attempt: int


ROLE_TYPES = {
    "MORCH": {"TASK_HANDOFF", "ACCEPTED"},
    "RORCH": {"DELIVERY", "REWORK_RESPONSE", "RORCH_ACK"},
    "QA": {"QA_REJECTED", "QA_PASS"},
    "UWS": {"RECOVERY", "CONTROL"},
}

ROUTES = {
    "TASK_HANDOFF": {"RORCH"},
    "DELIVERY": {"QA"},
    "REWORK_RESPONSE": {"QA"},
    "QA_REJECTED": {"RORCH"},
    "QA_PASS": {"MORCH"},
    "ACCEPTED": {"RORCH"},
    "RORCH_ACK": {"MORCH"},
    "RECOVERY": {"MORCH", "RORCH", "QA"},
    "CONTROL": {"MORCH", "RORCH", "QA"},
}


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS actors(
  actor_id TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  key_hex TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS used_nonces(
  producer_actor TEXT NOT NULL,
  nonce TEXT NOT NULL,
  message_id TEXT NOT NULL,
  used_at REAL NOT NULL,
  PRIMARY KEY(producer_actor, nonce)
);
CREATE TABLE IF NOT EXISTS outbox(
  message_id TEXT PRIMARY KEY,
  producer_actor TEXT NOT NULL,
  recipient_actor TEXT NOT NULL,
  message_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  nonce TEXT NOT NULL,
  issued_at REAL NOT NULL,
  signature TEXT NOT NULL,
  state TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL,
  lease_owner TEXT,
  claim_token TEXT,
  lease_expires_at REAL,
  last_error TEXT,
  consumer_receipt TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  FOREIGN KEY(producer_actor) REFERENCES actors(actor_id),
  FOREIGN KEY(recipient_actor) REFERENCES actors(actor_id)
);
CREATE TABLE IF NOT EXISTS inbox(
  message_id TEXT PRIMARY KEY,
  consumer_actor TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  state TEXT NOT NULL,
  claim_token TEXT NOT NULL,
  received_at REAL NOT NULL,
  decided_at REAL,
  decision_reason TEXT,
  consumer_receipt TEXT,
  FOREIGN KEY(message_id) REFERENCES outbox(message_id),
  FOREIGN KEY(consumer_actor) REFERENCES actors(actor_id)
);
CREATE TABLE IF NOT EXISTS audit(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp REAL NOT NULL,
  action TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  message_id TEXT,
  detail_json TEXT NOT NULL,
  detail_sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
"""


class SecureTransport:
    def __init__(self, db_path: str | Path, bootstrap_secret: bytes, *, replay_window_s: float = 300.0):
        if not bootstrap_secret:
            raise ValueError("bootstrap secret is required")
        self.db_path = str(db_path)
        self._bootstrap_digest = hashlib.sha256(bootstrap_secret).digest()
        self.replay_window_s = replay_window_s
        with self._read_conn() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=20000")
        return conn

    @contextmanager
    def _read_conn(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _digest(data: str) -> str:
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    @staticmethod
    def _sign(key: bytes, canonical: str) -> str:
        return hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _canonical(message_id: str, producer: str, recipient: str, message_type: str,
                   payload_sha256: str, nonce: str, issued_at: float) -> str:
        return "|".join((message_id, producer, recipient, message_type, payload_sha256, nonce, f"{issued_at:.6f}"))

    def _audit(self, conn: sqlite3.Connection, action: str, actor: str,
               message_id: str | None, detail: dict[str, Any]) -> None:
        detail_json = self._json(detail)
        conn.execute(
            "INSERT INTO audit(timestamp,action,actor_id,message_id,detail_json,detail_sha256) VALUES(?,?,?,?,?,?)",
            (time.time(), action, actor, message_id, detail_json, self._digest(detail_json)),
        )

    def register_actor(self, actor_id: str, role: str, key: bytes, *, bootstrap_secret: bytes) -> None:
        if not hmac.compare_digest(hashlib.sha256(bootstrap_secret).digest(), self._bootstrap_digest):
            raise AuthenticationError("invalid bootstrap capability")
        if role not in ROLE_TYPES or not actor_id or not key:
            raise ValueError("invalid actor registration")
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO actors(actor_id,role,key_hex,active,created_at) VALUES(?,?,?,?,?)",
                (actor_id, role, key.hex(), 1, time.time()),
            )
            self._audit(conn, "ACTOR_REGISTERED", "BOOTSTRAP", None, {"actor_id": actor_id, "role": role})

    def _authenticate(self, conn: sqlite3.Connection, actor_id: str, key: bytes) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM actors WHERE actor_id=?", (actor_id,)).fetchone()
        if row is None or not row["active"] or not hmac.compare_digest(bytes.fromhex(row["key_hex"]), key):
            raise AuthenticationError("actor authentication failed")
        return row

    def enqueue(self, *, producer: str, producer_key: bytes, recipient: str,
                message_type: str, payload: dict[str, Any], message_id: str,
                nonce: str, issued_at: float | None = None, max_retries: int = 3,
                now: float | None = None) -> str:
        now = time.time() if now is None else now
        issued_at = now if issued_at is None else issued_at
        payload_json = self._json(payload)
        payload_hash = self._digest(payload_json)
        if abs(now - issued_at) > self.replay_window_s:
            raise ReplayError("message timestamp outside replay window")
        if max_retries < 1 or max_retries > 10:
            raise ValueError("max_retries must be between 1 and 10")
        with self._tx() as conn:
            producer_row = self._authenticate(conn, producer, producer_key)
            recipient_row = conn.execute("SELECT * FROM actors WHERE actor_id=? AND active=1", (recipient,)).fetchone()
            if recipient_row is None:
                raise AuthorizationError("unknown recipient")
            role = producer_row["role"]
            if message_type not in ROLE_TYPES[role]:
                raise AuthorizationError(f"role {role} cannot emit {message_type}")
            if recipient_row["role"] not in ROUTES.get(message_type, set()):
                raise AuthorizationError("message route is not permitted")
            existing = conn.execute("SELECT * FROM outbox WHERE message_id=?", (message_id,)).fetchone()
            if existing is not None:
                if (existing["payload_sha256"] == payload_hash and existing["producer_actor"] == producer
                        and existing["recipient_actor"] == recipient and existing["message_type"] == message_type):
                    self._audit(conn, "DUPLICATE_SUPPRESSED", producer, message_id, {"payload_sha256": payload_hash})
                    return "DUPLICATE"
                conn.execute("UPDATE outbox SET state='CONFLICT',last_error=?,updated_at=? WHERE message_id=?",
                             ("same message_id with different content or envelope", now, message_id))
                self._audit(conn, "MESSAGE_CONFLICT", producer, message_id, {"incoming_sha256": payload_hash})
                conn.commit()  # conflict evidence must survive the rejected enqueue
                raise ConflictError("same message_id has conflicting content")
            try:
                conn.execute("INSERT INTO used_nonces VALUES(?,?,?,?)", (producer, nonce, message_id, now))
            except sqlite3.IntegrityError as exc:
                self._audit(conn, "REPLAY_REJECTED", producer, message_id, {"nonce": nonce})
                raise ReplayError("producer nonce was already used") from exc
            canonical = self._canonical(message_id, producer, recipient, message_type, payload_hash, nonce, issued_at)
            signature = self._sign(producer_key, canonical)
            conn.execute(
                """INSERT INTO outbox(message_id,producer_actor,recipient_actor,message_type,payload_json,
                   payload_sha256,nonce,issued_at,signature,state,max_retries,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,'PENDING',?,?,?)""",
                (message_id, producer, recipient, message_type, payload_json, payload_hash, nonce,
                 issued_at, signature, max_retries, now, now),
            )
            self._audit(conn, "OUTBOX_ENQUEUED", producer, message_id, {"state": "PENDING", "payload_sha256": payload_hash})
            return "PENDING"

    def claim(self, *, consumer: str, consumer_key: bytes, lease_s: float = 30.0,
              now: float | None = None) -> Delivery | None:
        now = time.time() if now is None else now
        with self._tx() as conn:
            consumer_row = self._authenticate(conn, consumer, consumer_key)
            role = consumer_row["role"]
            rows = conn.execute(
                """SELECT * FROM outbox WHERE recipient_actor=? AND
                   (state='PENDING' OR (state='CLAIMED' AND lease_expires_at<=?))
                   ORDER BY created_at,message_id""", (consumer, now)).fetchall()
            for row in rows:
                if row["attempt_count"] >= row["max_retries"]:
                    conn.execute("UPDATE outbox SET state='DEAD',last_error='retry limit exhausted',updated_at=? WHERE message_id=?",
                                 (now, row["message_id"]))
                    self._audit(conn, "RETRY_EXHAUSTED", "UWS", row["message_id"], {"attempts": row["attempt_count"]})
                    continue
                if role not in ROUTES.get(row["message_type"], set()):
                    conn.execute("UPDATE outbox SET state='NACKED',last_error='recipient role mismatch',updated_at=? WHERE message_id=?",
                                 (now, row["message_id"]))
                    self._audit(conn, "ROUTE_REJECTED", consumer, row["message_id"], {"role": role})
                    continue
                producer = conn.execute("SELECT key_hex FROM actors WHERE actor_id=? AND active=1", (row["producer_actor"],)).fetchone()
                canonical = self._canonical(row["message_id"], row["producer_actor"], row["recipient_actor"],
                                            row["message_type"], row["payload_sha256"], row["nonce"], row["issued_at"])
                if producer is None or not hmac.compare_digest(row["signature"], self._sign(bytes.fromhex(producer["key_hex"]), canonical)):
                    conn.execute("UPDATE outbox SET state='NACKED',last_error='invalid producer signature',updated_at=? WHERE message_id=?",
                                 (now, row["message_id"]))
                    self._audit(conn, "SIGNATURE_REJECTED", consumer, row["message_id"], {})
                    continue
                claim_token = secrets.token_hex(24)
                lease_expires = now + lease_s
                attempt = row["attempt_count"] + 1
                conn.execute(
                    "UPDATE outbox SET state='CLAIMED',attempt_count=?,lease_owner=?,claim_token=?,lease_expires_at=?,updated_at=? WHERE message_id=?",
                    (attempt, consumer, claim_token, lease_expires, now, row["message_id"]),
                )
                conn.execute(
                    """INSERT INTO inbox(message_id,consumer_actor,payload_sha256,state,claim_token,received_at)
                       VALUES(?,?,?,'PROCESSING',?,?) ON CONFLICT(message_id) DO UPDATE SET
                       consumer_actor=excluded.consumer_actor,payload_sha256=excluded.payload_sha256,
                       state='PROCESSING',claim_token=excluded.claim_token,received_at=excluded.received_at,
                       decided_at=NULL,decision_reason=NULL,consumer_receipt=NULL""",
                    (row["message_id"], consumer, row["payload_sha256"], claim_token, now),
                )
                self._audit(conn, "INBOX_CLAIMED", consumer, row["message_id"], {"attempt": attempt, "lease_expires_at": lease_expires})
                return Delivery(row["message_id"], row["producer_actor"], consumer, row["message_type"],
                                json.loads(row["payload_json"]), row["payload_sha256"], claim_token, lease_expires, attempt)
            return None

    def _validate_claim(self, conn: sqlite3.Connection, consumer: str, key: bytes,
                        message_id: str, claim_token: str, now: float) -> tuple[sqlite3.Row, sqlite3.Row]:
        self._authenticate(conn, consumer, key)
        out = conn.execute("SELECT * FROM outbox WHERE message_id=?", (message_id,)).fetchone()
        inbox = conn.execute("SELECT * FROM inbox WHERE message_id=?", (message_id,)).fetchone()
        if out is None or inbox is None or out["lease_owner"] != consumer or inbox["consumer_actor"] != consumer:
            raise LeaseError("consumer does not own delivery")
        if not hmac.compare_digest(out["claim_token"] or "", claim_token) or not hmac.compare_digest(inbox["claim_token"], claim_token):
            raise LeaseError("claim token mismatch")
        if out["lease_expires_at"] is None or out["lease_expires_at"] < now:
            raise LeaseError("lease expired")
        return out, inbox

    def ack(self, *, consumer: str, consumer_key: bytes, message_id: str,
            claim_token: str, now: float | None = None) -> str:
        """Consumer commits its ACK. UWS reconciles the producer outbox later."""
        now = time.time() if now is None else now
        with self._tx() as conn:
            out, _ = self._validate_claim(conn, consumer, consumer_key, message_id, claim_token, now)
            receipt_body = f"ACK|{message_id}|{out['payload_sha256']}|{claim_token}"
            receipt = self._sign(consumer_key, receipt_body)
            conn.execute("UPDATE inbox SET state='ACKED',decided_at=?,consumer_receipt=? WHERE message_id=?",
                         (now, receipt, message_id))
            conn.execute("UPDATE outbox SET state='AWAITING_RECONCILIATION',consumer_receipt=?,updated_at=? WHERE message_id=?",
                         (receipt, now, message_id))
            self._audit(conn, "CONSUMER_ACK", consumer, message_id, {"receipt_sha256": self._digest(receipt)})
            return receipt

    def nack(self, *, consumer: str, consumer_key: bytes, message_id: str,
             claim_token: str, reason: str, retryable: bool = True,
             now: float | None = None) -> str:
        now = time.time() if now is None else now
        with self._tx() as conn:
            out, _ = self._validate_claim(conn, consumer, consumer_key, message_id, claim_token, now)
            next_state = "PENDING" if retryable and out["attempt_count"] < out["max_retries"] else "DEAD"
            conn.execute("UPDATE inbox SET state='NACKED',decided_at=?,decision_reason=? WHERE message_id=?",
                         (now, reason, message_id))
            conn.execute(
                "UPDATE outbox SET state=?,lease_owner=NULL,claim_token=NULL,lease_expires_at=NULL,last_error=?,updated_at=? WHERE message_id=?",
                (next_state, reason, now, message_id),
            )
            self._audit(conn, "CONSUMER_NACK", consumer, message_id, {"reason": reason, "next_state": next_state})
            return next_state

    def reconcile(self, *, now: float | None = None) -> dict[str, int]:
        """UWS repairs expired claims and finalizes verified consumer receipts."""
        now = time.time() if now is None else now
        stats = {"acked": 0, "requeued": 0, "dead": 0, "invalid_receipt": 0}
        with self._tx() as conn:
            pending_ack = conn.execute("SELECT o.*,i.consumer_actor,i.claim_token AS inbox_claim,i.consumer_receipt AS inbox_receipt FROM outbox o JOIN inbox i USING(message_id) WHERE o.state='AWAITING_RECONCILIATION' AND i.state='ACKED'").fetchall()
            for row in pending_ack:
                actor = conn.execute("SELECT key_hex FROM actors WHERE actor_id=? AND active=1", (row["consumer_actor"],)).fetchone()
                expected = "" if actor is None else self._sign(bytes.fromhex(actor["key_hex"]), f"ACK|{row['message_id']}|{row['payload_sha256']}|{row['inbox_claim']}")
                if actor is None or not hmac.compare_digest(row["inbox_receipt"] or "", expected) or not hmac.compare_digest(row["consumer_receipt"] or "", expected):
                    conn.execute("UPDATE outbox SET state='NACKED',last_error='invalid consumer receipt',updated_at=? WHERE message_id=?", (now, row["message_id"]))
                    self._audit(conn, "ACK_RECONCILE_REJECTED", "UWS", row["message_id"], {})
                    stats["invalid_receipt"] += 1
                else:
                    conn.execute("UPDATE outbox SET state='ACKED',updated_at=? WHERE message_id=?", (now, row["message_id"]))
                    self._audit(conn, "ACK_RECONCILED", "UWS", row["message_id"], {})
                    stats["acked"] += 1
            expired = conn.execute("SELECT * FROM outbox WHERE state='CLAIMED' AND lease_expires_at<=?", (now,)).fetchall()
            for row in expired:
                state = "DEAD" if row["attempt_count"] >= row["max_retries"] else "PENDING"
                conn.execute("UPDATE outbox SET state=?,lease_owner=NULL,claim_token=NULL,lease_expires_at=NULL,last_error='lease expired',updated_at=? WHERE message_id=?", (state, now, row["message_id"]))
                conn.execute("UPDATE inbox SET state='NACKED',decided_at=?,decision_reason='lease expired' WHERE message_id=?", (now, row["message_id"]))
                self._audit(conn, "LEASE_RECOVERED", "UWS", row["message_id"], {"next_state": state})
                stats["dead" if state == "DEAD" else "requeued"] += 1
        return stats

    def state(self, message_id: str) -> dict[str, Any]:
        with self._read_conn() as conn:
            out = conn.execute("SELECT * FROM outbox WHERE message_id=?", (message_id,)).fetchone()
            inbox = conn.execute("SELECT * FROM inbox WHERE message_id=?", (message_id,)).fetchone()
            return {"outbox": dict(out) if out else None, "inbox": dict(inbox) if inbox else None}

    def counts(self) -> dict[str, int]:
        with self._read_conn() as conn:
            return {
                "outbox": conn.execute("SELECT count(*) FROM outbox").fetchone()[0],
                "inbox": conn.execute("SELECT count(*) FROM inbox").fetchone()[0],
                "audit": conn.execute("SELECT count(*) FROM audit").fetchone()[0],
            }

    def audit_rows(self) -> list[dict[str, Any]]:
        with self._read_conn() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM audit ORDER BY seq")]

    def attempt_audit_mutation(self, *, actor: str, actor_key: bytes, seq: int) -> None:
        """Adversarial API: authentication cannot bypass append-only DB trigger."""
        with self._tx() as conn:
            row = self._authenticate(conn, actor, actor_key)
            if row["role"] != "UWS":
                raise AuthorizationError("only UWS may request ledger maintenance")
            conn.execute("UPDATE audit SET action='TAMPERED' WHERE seq=?", (seq,))


