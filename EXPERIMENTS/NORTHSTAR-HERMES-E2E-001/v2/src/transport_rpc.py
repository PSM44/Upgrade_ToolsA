#!/usr/bin/env python3
"""Ephemeral Unix-socket boundary around SecureTransport.

The daemon alone sees the SQLite ledger and actor registry.  Role clients see
only one capability file and the Unix socket, and must initiate their own
enqueue/claim/ACK operations.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import socket
from pathlib import Path

from secure_transport import SecureTransport

ACTORS = ("MORCH", "RORCH", "QA", "UWS")
BOOTSTRAP = b"northstar-v3-ephemeral-transport-authority"


def send_json(sock: socket.socket, value: dict) -> None:
    sock.sendall(json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n")


def recv_json(sock: socket.socket) -> dict:
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    return json.loads(data.decode())


class Server:
    def __init__(self, db: Path, key_dir: Path):
        key_dir.mkdir(parents=True, exist_ok=True)
        self.keys: dict[str, bytes] = {}
        for actor in ACTORS:
            path = key_dir / f"{actor.lower()}.key"
            if not path.exists():
                path.write_bytes(os.urandom(32))
            self.keys[actor] = path.read_bytes()
        self.transport = SecureTransport(db, BOOTSTRAP)
        import sqlite3
        with sqlite3.connect(db) as conn:
            registered = {row[0] for row in conn.execute("SELECT actor_id FROM actors")}
        for actor in ACTORS:
            if actor not in registered:
                self.transport.register_actor(actor, actor, self.keys[actor], bootstrap_secret=BOOTSTRAP)
        self.running = True

    def authenticate(self, request: dict, required: str | None = None) -> tuple[str, bytes]:
        actor = str(request.get("actor", ""))
        try:
            supplied = bytes.fromhex(str(request.get("key", "")))
            expected = self.keys[actor]
        except (KeyError, ValueError):
            raise PermissionError("unknown actor capability")
        if not hmac.compare_digest(supplied, expected):
            raise PermissionError("actor capability mismatch")
        if required is not None and actor != required:
            raise PermissionError(f"operation requires {required}")
        return actor, supplied

    def handle(self, request: dict) -> dict:
        op = request.get("op")
        actor, key = self.authenticate(request, "UWS" if op in {"reconcile", "redrive_dead", "snapshot", "shutdown"} else None)
        if op == "send":
            state = self.transport.enqueue(
                producer=actor, producer_key=key, recipient=request["recipient"],
                message_type=request["message_type"], payload=request["payload"],
                message_id=request["message_id"], nonce=request["nonce"],
            )
            return {"ok": True, "state": state, "actor": actor}
        if op in {"claim", "consume"}:
            lease_s = max(1, min(int(request.get("lease_seconds", 30)), 900))
            delivery = self.transport.claim(consumer=actor, consumer_key=key, lease_s=lease_s)
            if delivery is None:
                return {"ok": False, "error": "NO_DELIVERY"}
            if delivery.message_id != request["expected_message_id"]:
                self.transport.nack(consumer=actor, consumer_key=key, message_id=delivery.message_id,
                                    claim_token=delivery.claim_token, reason="unexpected message", retryable=True)
                return {"ok": False, "error": "UNEXPECTED_MESSAGE", "observed": delivery.message_id}
            response = {"ok": True, "actor": actor, "message_id": delivery.message_id,
                    "producer": delivery.producer, "message_type": delivery.message_type,
                    "payload": delivery.payload, "payload_sha256": delivery.payload_sha256,
                    "claim_token": delivery.claim_token, "lease_expires_at": delivery.lease_expires_at,
                    "attempt": delivery.attempt}
            if op == "consume":
                receipt = self.transport.ack(consumer=actor, consumer_key=key, message_id=delivery.message_id,
                                             claim_token=delivery.claim_token)
                response["receipt_sha256"] = self.transport._digest(receipt)
            return response
        if op == "ack":
            receipt = self.transport.ack(consumer=actor, consumer_key=key,
                                         message_id=request["message_id"], claim_token=request["claim_token"])
            return {"ok": True, "actor": actor, "message_id": request["message_id"],
                    "receipt_sha256": self.transport._digest(receipt)}
        if op == "nack":
            state = self.transport.nack(consumer=actor, consumer_key=key,
                                        message_id=request["message_id"], claim_token=request["claim_token"],
                                        reason=request.get("reason", "worker failure"),
                                        retryable=bool(request.get("retryable", True)))
            return {"ok": True, "actor": actor, "message_id": request["message_id"], "state": state}
        if op == "reconcile":
            return {"ok": True, "actor": actor, "stats": self.transport.reconcile()}
        if op == "redrive_dead":
            state = self.transport.redrive_dead(request["message_id"], reason=request.get("reason", "repaired implementation"))
            return {"ok": True, "actor": actor, "message_id": request["message_id"], "state": state}
        if op == "snapshot":
            return {"ok": True, "counts": self.transport.counts(),
                    "audit_rows": len(self.transport.audit_rows()),
                    "messages": request.get("message_ids") and
                        {m: self.transport.state(m) for m in request["message_ids"]}}
        if op == "shutdown":
            self.running = False
            return {"ok": True, "actor": actor}
        raise ValueError(f"unsupported operation: {op}")


def serve(args: argparse.Namespace) -> int:
    socket_path = Path(args.socket)
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.parent.chmod(0o700)
    if socket_path.exists():
        socket_path.unlink()
    server = Server(Path(args.db), Path(args.key_dir))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(socket_path)); socket_path.chmod(0o600); listener.listen(16); listener.settimeout(1.0)
        while server.running:
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            with conn:
                try:
                    response = server.handle(recv_json(conn))
                except Exception as exc:
                    response = {"ok": False, "error": type(exc).__name__, "detail": str(exc)}
                send_json(conn, response)
    if socket_path.exists():
        socket_path.unlink()
    return 0


def client(args: argparse.Namespace) -> int:
    key = Path(args.key).read_bytes().hex()
    request = {"op": args.operation, "actor": args.actor, "key": key}
    if args.operation == "send":
        request.update({
            "recipient": args.recipient, "message_type": args.message_type,
            "message_id": args.message_id, "nonce": args.nonce,
            "payload": json.loads(Path(args.payload).read_text(encoding="utf-8")),
        })
    elif args.operation in {"claim", "consume"}:
        request["expected_message_id"] = args.message_id
    elif args.operation in {"ack", "nack", "redrive_dead"}:
        request["message_id"] = args.message_id
        request["claim_token"] = args.claim_token
        if args.operation == "nack":
            request["reason"] = args.reason
            request["retryable"] = not args.non_retryable
    elif args.operation == "snapshot":
        request["message_ids"] = args.message_ids or []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(args.socket); send_json(sock, request); response = recv_json(sock)
    print(json.dumps(response, sort_keys=True))
    return 0 if response.get("ok") else 3


def main() -> int:
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("serve"); s.add_argument("--db", required=True); s.add_argument("--key-dir", required=True); s.add_argument("--socket", required=True)
    c = sub.add_parser("client"); c.add_argument("--socket", required=True); c.add_argument("--key", required=True); c.add_argument("--actor", required=True, choices=ACTORS); c.add_argument("operation", choices=("send", "claim", "consume", "ack", "nack", "reconcile", "redrive_dead", "snapshot", "shutdown")); c.add_argument("--recipient"); c.add_argument("--message-type"); c.add_argument("--message-id"); c.add_argument("--nonce"); c.add_argument("--payload"); c.add_argument("--message-ids", nargs="*"); c.add_argument("--claim-token"); c.add_argument("--reason"); c.add_argument("--non-retryable", action="store_true")
    args = parser.parse_args()
    return serve(args) if args.command == "serve" else client(args)


if __name__ == "__main__":
    raise SystemExit(main())

