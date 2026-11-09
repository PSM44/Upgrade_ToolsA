#!/usr/bin/env python3
"""One-shot authenticated role consumer for the persistent control plane."""

from __future__ import annotations

import argparse, hashlib, json, socket, subprocess, time
from pathlib import Path
from typing import Any


def rpc(socket_path: str, actor: str, key: bytes, op: str, **fields: Any) -> dict[str, Any]:
    req = {"op": op, "actor": actor, "key": key.hex(), **fields}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(socket_path)
        sock.sendall(json.dumps(req, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk: break
            data += chunk
    response = json.loads(data.decode())
    if not response.get("ok"): raise RuntimeError(f"RPC {actor}/{op}: {response}")
    return response


def emit(sock: str, actor: str, key: bytes, recipient: str, kind: str,
         objective_id: str, payload: dict[str, Any], edge: str) -> None:
    mid = f"{objective_id}:{edge}"
    nonce = hashlib.sha256(f"{mid}|{actor}|{recipient}".encode()).hexdigest()
    rpc(sock, actor, key, "send", recipient=recipient, message_type=kind,
        message_id=mid, nonce=nonce, payload=payload)


BUGGY = 'def is_palindrome(value: str) -> bool:\n    normalized=value.lower().replace(" ", "")\n    return normalized == normalized[::-1]\n'
FIXED = 'import re\ndef is_palindrome(value: str) -> bool:\n    normalized=re.sub(r"[^a-z0-9]", "", value.lower())\n    return normalized == normalized[::-1]\n'
TEST = 'import unittest\nfrom app import is_palindrome\nclass T(unittest.TestCase):\n def test_basic(self):\n  self.assertTrue(is_palindrome("level")); self.assertFalse(is_palindrome("OpenAI"))\n'


def tests(app: Path) -> dict[str, Any]:
    p = subprocess.run(["/usr/bin/python3", "-m", "unittest", "-v", "test_app.py"], cwd=app,
                       text=True, capture_output=True, timeout=30)
    return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def qa(app: Path) -> dict[str, Any]:
    code = "from app import is_palindrome; assert is_palindrome('A man, a plan, a canal: Panama!'); assert is_palindrome(\"No 'x' in Nixon\"); assert not is_palindrome('OpenAI')"
    p = subprocess.run(["/usr/bin/python3", "-c", code], cwd=app, text=True, capture_output=True, timeout=30)
    return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "probe": "PALINDROME-ADVERSARIAL-v1"}


def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--actor", required=True, choices=("MORCH", "RORCH", "QA"))
    p.add_argument("--key", required=True, type=Path); p.add_argument("--socket", required=True)
    p.add_argument("--message-id", required=True); p.add_argument("--app", type=Path, default=Path("/role/app"))
    p.add_argument("--qa", type=Path, default=Path("/role/qa")); a = p.parse_args()
    key = a.key.read_bytes(); delivery = rpc(a.socket, a.actor, key, "consume", expected_message_id=a.message_id)
    payload = dict(delivery["payload"]); oid = str(payload["objective_id"]); kind = str(delivery["message_type"])
    a.qa.mkdir(parents=True, exist_ok=True)
    if a.actor == "RORCH" and kind == "TASK_HANDOFF":
        a.app.mkdir(parents=True, exist_ok=True); (a.app/"app.py").write_text(BUGGY, encoding="utf-8")
        (a.app/"test_app.py").write_text(TEST, encoding="utf-8"); result = tests(a.app)
        (a.qa/"rorch_round1.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        emit(a.socket,"RORCH",key,"QA","DELIVERY",oid,{"objective_id":oid,"round":1,"local_test":result["exit_code"],"artifact_sha256":hashlib.sha256((a.app/"app.py").read_bytes()).hexdigest()},"delivery-1")
    elif a.actor == "QA" and kind in {"DELIVERY", "REWORK_RESPONSE"}:
        result=qa(a.app); rnd=int(payload.get("round",1)); ev=a.qa/f"qa_round{rnd}.json"
        ev.write_text(json.dumps(result,indent=2),encoding="utf-8")
        if result["exit_code"]:
            emit(a.socket,"QA",key,"RORCH","QA_REJECTED",oid,{"objective_id":oid,"round":rnd,"evidence":str(ev),"reason":"punctuation normalization failed"},f"qa-rejected-{rnd}")
        else:
            emit(a.socket,"QA",key,"MORCH","QA_PASS",oid,{"objective_id":oid,"round":rnd,"evidence":str(ev),"verdict":"PASS"},f"qa-pass-{rnd}")
    elif a.actor == "RORCH" and kind == "QA_REJECTED":
        (a.app/"app.py").write_text(FIXED,encoding="utf-8"); result=tests(a.app)
        (a.qa/"rorch_round2.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        emit(a.socket,"RORCH",key,"QA","REWORK_RESPONSE",oid,{"objective_id":oid,"round":2,"local_test":result["exit_code"],"artifact_sha256":hashlib.sha256((a.app/"app.py").read_bytes()).hexdigest()},"rework-2")
    elif a.actor == "MORCH" and kind == "QA_PASS":
        if payload.get("verdict") != "PASS": raise RuntimeError("malformed QA_PASS")
        emit(a.socket,"MORCH",key,"RORCH","ACCEPTED",oid,{"objective_id":oid,"decision":"ACCEPTED","qa_evidence":payload.get("evidence")},"accepted")
    elif a.actor == "RORCH" and kind == "ACCEPTED":
        emit(a.socket,"RORCH",key,"MORCH","RORCH_ACK",oid,{"objective_id":oid,"decision":"ACKNOWLEDGED"},"rorch-ack")
    elif not (a.actor == "MORCH" and kind == "RORCH_ACK"):
        raise RuntimeError(f"unsupported {a.actor}/{kind}")
    print(json.dumps({"actor":a.actor,"message_id":a.message_id,"message_type":kind,"objective_id":oid,"completed_at":time.time()},sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())

