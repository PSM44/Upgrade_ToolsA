#!/usr/bin/env python3
"""One-shot authenticated role consumer for the persistent control plane."""

from __future__ import annotations

import argparse, hashlib, json, socket, subprocess, time
from pathlib import Path
from typing import Any

from opencode_adapter import OpenCodeAdapter


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


def model_rpc(socket_path: str, actor: str, key: bytes, request_id: str,
              stage: str, prompt: str, **extra: Any) -> dict[str, Any]:
    request={"op":"call","actor":actor,"key":key.hex(),"request_id":request_id,
             "stage":stage,"prompt":prompt,**extra}
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.connect(socket_path);sock.sendall(json.dumps(request,sort_keys=True,separators=(",",":")).encode()+b"\n")
        data=b""
        while not data.endswith(b"\n"):
            part=sock.recv(65536)
            if not part:break
            data+=part
    response=json.loads(data.decode())
    if not response.get("ok"):raise RuntimeError(f"model broker {actor}/{stage}: {response}")
    return response


def emit(sock: str, actor: str, key: bytes, recipient: str, kind: str,
         objective_id: str, payload: dict[str, Any], edge: str) -> None:
    mid = f"{objective_id}:{edge}"
    nonce = hashlib.sha256(f"{mid}|{actor}|{recipient}".encode()).hexdigest()
    rpc(sock, actor, key, "send", recipient=recipient, message_type=kind,
        message_id=mid, nonce=nonce, payload=payload)


BUGGY = 'def is_palindrome(value: str) -> bool:\n    normalized=value.lower().replace(" ", "")\n    return normalized == normalized[::-1]\n'
FIXED = 'import re\ndef is_palindrome(value: str) -> bool:\n    normalized=re.sub(r"[^a-z0-9]", "", value.lower())\n    return normalized == normalized[::-1]\n'
SCAFFOLD = 'def is_palindrome(value: str) -> bool:\n    raise NotImplementedError("implement the assigned behavior")\n'
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
    p.add_argument("--dev", type=Path, default=Path("/role/dev")); p.add_argument("--qa", type=Path, default=Path("/role/qa"))
    p.add_argument("--backend",choices=("deterministic","model-broker","hermes","opencode"),default="deterministic");p.add_argument("--model-socket")
    p.add_argument("--opencode-bin", type=Path, default=Path("/opt/opencode/opencode"))
    p.add_argument("--opencode-rorch-model", default="opencode/glm-5.3-flash-free")
    p.add_argument("--opencode-qa-model", default="opencode/beam-free")
    a = p.parse_args()
    key = a.key.read_bytes(); delivery = rpc(a.socket, a.actor, key, "claim", expected_message_id=a.message_id, lease_seconds=600)
    payload = dict(delivery["payload"]); oid = str(payload["objective_id"]); kind = str(delivery["message_type"])
    from p2_task import handle as handle_p2, safe_objective_id
    safe_objective_id(oid)
    p2_result = handle_p2(a, delivery, key, rpc, model_rpc, emit)
    if p2_result is not None:
        ack = rpc(a.socket, a.actor, key, "ack", message_id=a.message_id, claim_token=delivery["claim_token"])
        print(json.dumps({**p2_result,"actor":a.actor,"message_id":a.message_id,
                          "message_type":kind,"objective_id":oid,"receipt_sha256":ack["receipt_sha256"]},sort_keys=True))
        return 0
    adapter = OpenCodeAdapter(
        a.opencode_bin, a.opencode_rorch_model, a.opencode_qa_model
    ) if a.backend == "opencode" else None
    if a.actor == "RORCH" and kind == "TASK_HANDOFF":
        a.app.mkdir(parents=True, exist_ok=True)
        a.dev.mkdir(parents=True, exist_ok=True)
        broker=None
        if a.backend == "opencode":
            (a.app/"app.py").write_text(SCAFFOLD, encoding="utf-8")
            (a.app/"test_app.py").write_text(TEST, encoding="utf-8")
            broker=adapter.invoke(
                actor="RORCH", stage="BUILD", location=a.app, evidence_dir=a.dev,
                request_id=f"{oid}:rorch-build",
                prompt="Implement the assigned initial specification in app.py and run the existing unittest. The initial specification is intentionally narrow: palindrome matching is case-insensitive and ignores ASCII spaces only. Do not add punctuation behavior or broaden the tests. Modify only app.py or test_app.py and finish with the tests passing."
            )
        elif a.backend in {"hermes","model-broker"}:
            prompt="""You are RORCH round 1. Return JSON only: {\"files\":{\"app.py\":\"...\",\"test_app.py\":\"...\"}}. Build a Python palindrome function is_palindrome. This seeded first delivery must intentionally normalize only case and ASCII spaces, not punctuation, and tests must cover only basic palindrome and non-palindrome. No markdown."""
            broker=model_rpc(a.model_socket,a.actor,key,f"{oid}:rorch-build","BUILD",prompt)
            response=broker["result"]
            files=response.get("files",response)
            if set(files)!={"app.py","test_app.py"}:raise RuntimeError("RORCH model returned invalid file set")
            (a.app/"app.py").write_text(str(files["app.py"]),encoding="utf-8");(a.app/"test_app.py").write_text(str(files["test_app.py"]),encoding="utf-8")
        else:
            (a.app/"app.py").write_text(BUGGY, encoding="utf-8");(a.app/"test_app.py").write_text(TEST, encoding="utf-8")
        result = tests(a.app);result["model_receipt"]=broker and broker["receipt"]
        (a.dev/"rorch_round1.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        emit(a.socket,"RORCH",key,"QA","DELIVERY",oid,{"objective_id":oid,"round":1,"local_test":result["exit_code"],"artifact_sha256":hashlib.sha256((a.app/"app.py").read_bytes()).hexdigest()},"delivery-1")
    elif a.actor == "QA" and kind in {"DELIVERY", "REWORK_RESPONSE"}:
        a.qa.mkdir(parents=True, exist_ok=True)
        result=qa(a.app); rnd=int(payload.get("round",1)); ev=a.qa/f"qa_round{rnd}.json"
        if a.backend == "opencode":
            review=a.qa/f"review_round{rnd}";review.mkdir(parents=True,exist_ok=True)
            for name in ("app.py","test_app.py"):
                (review/name).write_bytes((a.app/name).read_bytes())
            expected="PASS" if result["exit_code"]==0 else "REJECTED"
            broker=adapter.invoke(
                actor="QA", stage=f"REVIEW{rnd}", location=review,
                evidence_dir=a.qa, request_id=f"{oid}:qa-review-{rnd}",
                prompt=f"Act as a read-only independent QA reviewer. Use read tools to inspect app.py and test_app.py. The separately executed punctuation adversarial probe exit code is {result['exit_code']}. Return exactly one JSON object with verdict {expected!r} and a concise reason. Do not edit files or run shell commands."
            )
            verdict=broker["json"] or {}
            if verdict.get("verdict")!=expected:
                raise RuntimeError("QA OpenCode verdict contradicts executed probe")
            result["model_verdict"]=verdict;result["model_receipt"]=broker["receipt"]
        elif a.backend in {"hermes","model-broker"}:
            code=(a.app/"app.py").read_text(encoding="utf-8")
            expected="PASS" if result["exit_code"]==0 else "REJECTED"
            prompt=f"""You are independent QA round {rnd}. Deterministic adversarial probe exit_code={result['exit_code']}. Review this code:\n{code}\nReturn JSON only: {{\"verdict\":\"REJECTED\" or \"PASS\",\"reason\":\"...\"}}. Verdict must agree with the executed probe. No markdown."""
            broker=model_rpc(a.model_socket,a.actor,key,f"{oid}:qa-review-{rnd}",f"REVIEW{rnd}",prompt)
            if broker["result"].get("verdict")!=expected:
                retry_prompt=f"""Bounded QA correction: the authoritative allowlisted punctuation probe exit_code={result['exit_code']} requires verdict {expected}. Return exactly one JSON object with verdict {expected!r} and a concise reason limited to that probe. Do not introduce Unicode or other requirements."""
                broker=model_rpc(a.model_socket,a.actor,key,f"{oid}:qa-review-{rnd}-retry-1",f"REVIEW{rnd}",retry_prompt)
            if broker["result"].get("verdict")!=expected:
                raise RuntimeError("QA model verdict contradicts executed probe after bounded retry")
            result["model_verdict"]=broker["result"];result["model_receipt"]=broker["receipt"]
        ev.write_text(json.dumps(result,indent=2),encoding="utf-8")
        if result["exit_code"]:
            emit(a.socket,"QA",key,"RORCH","QA_REJECTED",oid,{"objective_id":oid,"round":rnd,"evidence":str(ev),"reason":"punctuation normalization failed"},f"qa-rejected-{rnd}")
        else:
            emit(a.socket,"QA",key,"MORCH","QA_PASS",oid,{"objective_id":oid,"round":rnd,"evidence":str(ev),"verdict":"PASS"},f"qa-pass-{rnd}")
    elif a.actor == "RORCH" and kind == "QA_REJECTED":
        a.dev.mkdir(parents=True, exist_ok=True)
        broker=None
        if a.backend == "opencode":
            broker=adapter.invoke(
                actor="RORCH", stage="FIX", location=a.app, evidence_dir=a.dev,
                request_id=f"{oid}:rorch-fix",
                prompt="QA rejected the implementation because punctuation and other non-alphanumeric characters are not ignored. Correct app.py to normalize by removing every non-alphanumeric character, add focused punctuation tests to test_app.py, and run python3 -m unittest -v until it passes. Modify only app.py or test_app.py."
            )
        elif a.backend in {"hermes","model-broker"}:
            current=(a.app/"app.py").read_text(encoding="utf-8");tests_current=(a.app/"test_app.py").read_text(encoding="utf-8")
            prompt=f"""You are RORCH rework. QA rejected punctuation normalization. Current app.py:\n{current}\nCurrent test_app.py:\n{tests_current}\nReturn JSON only: {{\"files\":{{\"app.py\":\"...\",\"test_app.py\":\"...\"}}}}. Correctly ignore every non-alphanumeric character and add focused tests using Python unittest only; do not use pytest or third-party packages. No markdown."""
            broker=model_rpc(a.model_socket,a.actor,key,f"{oid}:rorch-fix","FIX",prompt)
            response=broker["result"]
            files=response.get("files",response)
            if set(files)!={"app.py","test_app.py"}:raise RuntimeError("RORCH fix returned invalid file set")
            (a.app/"app.py").write_text(str(files["app.py"]),encoding="utf-8");(a.app/"test_app.py").write_text(str(files["test_app.py"]),encoding="utf-8")
        else:(a.app/"app.py").write_text(FIXED,encoding="utf-8")
        result=tests(a.app);result["model_receipt"]=broker and broker["receipt"]
        (a.dev/"rorch_round2.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        emit(a.socket,"RORCH",key,"QA","REWORK_RESPONSE",oid,{"objective_id":oid,"round":2,"local_test":result["exit_code"],"artifact_sha256":hashlib.sha256((a.app/"app.py").read_bytes()).hexdigest()},"rework-2")
    elif a.actor == "MORCH" and kind == "QA_PASS":
        if payload.get("verdict") != "PASS": raise RuntimeError("malformed QA_PASS")
        emit(a.socket,"MORCH",key,"RORCH","ACCEPTED",oid,{"objective_id":oid,"decision":"ACCEPTED","qa_evidence":payload.get("evidence")},"accepted")
    elif a.actor == "RORCH" and kind == "ACCEPTED":
        emit(a.socket,"RORCH",key,"MORCH","RORCH_ACK",oid,{"objective_id":oid,"decision":"ACKNOWLEDGED"},"rorch-ack")
    elif not (a.actor == "MORCH" and kind == "RORCH_ACK"):
        raise RuntimeError(f"unsupported {a.actor}/{kind}")
    ack = rpc(a.socket, a.actor, key, "ack", message_id=a.message_id, claim_token=delivery["claim_token"])
    print(json.dumps({"actor":a.actor,"message_id":a.message_id,"message_type":kind,"objective_id":oid,"completed_at":time.time(),"receipt_sha256":ack["receipt_sha256"]},sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
