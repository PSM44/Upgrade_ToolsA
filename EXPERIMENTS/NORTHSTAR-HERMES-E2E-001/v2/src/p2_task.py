"""One bounded real coding task, with immutable independent acceptance probes."""
from __future__ import annotations
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

SPEC_ID = "retry_schedule_v1"
SPEC = {
    "id": SPEC_ID,
    "contract": "retry_delays(attempts, initial=1.0, factor=2.0, cap=30.0) returns a list of attempts exponential delays, each min(cap, initial * factor**i). attempts must be an int, not bool, 0..8. initial, factor, cap must be int or float, not bool, finite and positive; factor >= 1. Invalid inputs raise ValueError, including with attempts=0. No mutation, I/O, network, dependencies or side effects.",
    "files": ["app.py", "test_app.py"],
    "max_rounds": 2,
}
SPEC_SHA256 = hashlib.sha256(json.dumps(SPEC,sort_keys=True,separators=(",",":")).encode()).hexdigest()
SCAFFOLD = 'def retry_delays(attempts, initial=1.0, factor=2.0, cap=30.0):\n    raise NotImplementedError("Implement the assigned specification")\n'
TEST_SCAFFOLD = 'import unittest\nfrom app import retry_delays\n\nclass RetryScheduleTests(unittest.TestCase):\n    def test_empty(self):\n        self.assertEqual(retry_delays(0), [])\n'
PROBE = r'''
import math
from app import retry_delays
cases = [
    ((0,), {}, []), ((1,), {}, [1.0]), ((8,), {}, [1,2,4,8,16,30,30,30]),
    ((4,), {"initial":0.25,"factor":3,"cap":2}, [0.25,0.75,2,2]),
    ((3,), {"initial":12,"factor":1,"cap":5}, [5,5,5]),
    ((3,), {"initial":2,"factor":1,"cap":9}, [2,2,2]),
    ((3,), {"initial":1e308,"factor":1e308,"cap":1e307}, [1e307]*3),
]
for args, kwargs, expected in cases:
    actual=retry_delays(*args, **kwargs)
    assert isinstance(actual,list), ("list required",actual)
    assert actual==expected, (args,kwargs,actual,expected)
    assert all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in actual)
invalid_attempts=[True,False,-1,9,1.0,"2",None]
for value in invalid_attempts:
    try: retry_delays(value)
    except ValueError: pass
    else: raise AssertionError(("attempts must reject",repr(value)))
for name in ("initial","factor","cap"):
    invalid=[True,False,0,-1,float("inf"),float("-inf"),float("nan"),"2",None]
    if name=="factor": invalid += [0.5]
    for value in invalid:
        for attempts in (0,2):
            try: retry_delays(attempts,**{name:value})
            except ValueError: pass
            else: raise AssertionError(("must reject",name,repr(value),attempts))
print("RETRY_SCHEDULE_PROBE_PASS")
'''

def safe_objective_id(value: str) -> str:
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}",value):
        raise ValueError("unsafe objective_id")
    return value

def validate_fixture(payload: dict) -> None:
    safe_objective_id(payload["objective_id"])
    if payload.get("fixture_spec") != SPEC_ID or payload.get("fixture_sha256") != SPEC_SHA256:
        raise ValueError("unknown or mismatched task specification")

def digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("task artifact must be a regular file of at most 65536 bytes")
    return hashlib.sha256(path.read_bytes()).hexdigest()

def isolated_command(app: Path, argv: list[str]) -> list[str]:
    return ["/usr/bin/bwrap","--unshare-all","--die-with-parent","--new-session",
            "--cap-drop","ALL","--clearenv","--ro-bind","/usr","/usr",
            "--symlink","usr/bin","/bin","--symlink","usr/lib","/lib",
            "--symlink","usr/lib64","/lib64","--ro-bind",str(app),"/app",
            "--tmpfs","/tmp","--proc","/proc","--dev","/dev",
            "--setenv","HOME","/tmp","--setenv","PATH","/usr/bin:/bin",
            "--setenv","PYTHONDONTWRITEBYTECODE","1","--chdir","/app",*argv]

def run_check(app: Path, argv: list[str]) -> dict:
    try:
        p=subprocess.run(isolated_command(app,argv),text=True,capture_output=True,timeout=20)
        return {"exit_code":p.returncode,"stdout":p.stdout[-6000:],"stderr":p.stderr[-6000:]}
    except subprocess.TimeoutExpired:
        return {"exit_code":124,"stdout":"","stderr":"isolated check timed out"}

def checks(app: Path) -> dict:
    before={name:digest(app/name) for name in SPEC["files"]}
    local=run_check(app,["/usr/bin/python3","-B","-m","unittest","-v","test_app.py"])
    probe=run_check(app,["/usr/bin/python3","-B","-c",PROBE])
    after={name:digest(app/name) for name in SPEC["files"]}
    return {"local_test":local,"probe":probe,"artifact_sha256":after["app.py"],
            "test_sha256":after["test_app.py"],"unchanged":before==after,
            "test_count":int(re.search(r"Ran (\d+) tests?",local["stderr"]).group(1)) if re.search(r"Ran (\d+) tests?",local["stderr"]) else 0,
            "probe_completed":"RETRY_SCHEDULE_PROBE_PASS" in probe["stdout"].splitlines(),
            "tests_extended":(app/"test_app.py").read_text()!=TEST_SCAFFOLD}

def checks_pass(result: dict) -> bool:
    return (result["local_test"]["exit_code"]==0 and result["probe"]["exit_code"]==0
            and result["unchanged"] and result["tests_extended"]
            and result.get("test_count",0)>=4 and result.get("probe_completed") is True)

def qa_pass(result: dict, verdict: dict) -> bool:
    return checks_pass(result) and verdict.get("verdict")=="PASS"

def build_prompt(objective: str, feedback: str | None = None) -> str:
    return ("Implement this bounded task by actually editing app.py and test_app.py using editing tools. "
            "Read the existing files first. Add at least four focused standard-library unittest tests for valid and invalid inputs. "
            "Only modify these two regular files; do not add files or dependencies. Do not run code or shell tools; "
            "the isolated executor will run the tests and independent probes after your edits. "
            + SPEC["contract"] + "\nHUMAN objective: " + objective
            + ("\nQA feedback: "+feedback if feedback else "")
            + "\nFinish with a short summary of your edits.")

def handle(a, delivery: dict, key: bytes, rpc, model_rpc, emit) -> dict | None:
    payload=dict(delivery["payload"])
    if payload.get("fixture_spec") != SPEC_ID:
        return None
    validate_fixture(payload)
    oid=payload["objective_id"]; kind=delivery["message_type"]
    base={"objective_id":oid,"fixture_spec":SPEC_ID,"fixture_sha256":SPEC_SHA256}
    objective=str(payload.get("objective","Implement "+SPEC_ID))
    context={**base,"objective":objective}
    def send(recipient,kind,body,edge):
        emit(a.socket,a.actor,key,recipient,kind,oid,{**context,**body},edge)
    if a.actor=="RORCH" and kind in {"TASK_HANDOFF","QA_REJECTED"}:
        rnd=1 if kind=="TASK_HANDOFF" else int(payload.get("round",0))+1
        if rnd>2:
            return {**base,"terminal_failure":True,"reason":"QA rejected after bounded two rounds",
                    "qa_reason":payload.get("reason"),"round":2}
        if rnd<1 or (kind=="QA_REJECTED" and rnd!=2):
            raise ValueError("invalid P2 round")
        a.app.mkdir(parents=True,exist_ok=True);a.dev.mkdir(parents=True,exist_ok=True)
        if rnd==1:
            for name,content in (("app.py",SCAFFOLD),("test_app.py",TEST_SCAFFOLD)):
                target=a.app/name
                if target.is_symlink():raise ValueError("unsafe scaffold symlink")
                if not target.exists():target.write_text(content,encoding="utf-8")
        stage="BUILD" if rnd==1 else "FIX"
        response=model_rpc(a.model_socket,a.actor,key,f"{oid}:rorch-"+("build" if rnd==1 else "fix"),
                           stage,build_prompt(objective,payload.get("reason")),op="edit",objective_id=oid)
        if response.get("result",{}).get("edited") is not True:
            raise RuntimeError("broker did not attest real editing")
        result=checks(a.app);result["model_receipt"]=response.get("receipt")
        (a.dev/f"rorch_round{rnd}.json").write_text(json.dumps(result,sort_keys=True,indent=2))
        send("QA","DELIVERY" if rnd==1 else "REWORK_RESPONSE",
             {"round":rnd,"artifact_sha256":result["artifact_sha256"],"test_sha256":result["test_sha256"]},
             "delivery-1" if rnd==1 else "rework-2")
    elif a.actor=="QA" and kind in {"DELIVERY","REWORK_RESPONSE"}:
        rnd=int(payload.get("round",0))
        if rnd not in (1,2):raise ValueError("invalid P2 review round")
        result=checks(a.app)
        if payload.get("artifact_sha256")!=result["artifact_sha256"] or payload.get("test_sha256")!=result["test_sha256"]:
            raise RuntimeError("delivery artifact changed before QA")
        prompt=("Independently review this task, implementation and tests. Return JSON with verdict PASS or REJECTED "
                "and reason. You may reject for any concrete contract violation even when probes pass. "
                "Never claim PASS when executed checks fail. Do not call tools.\nContract: "+SPEC["contract"]
                +"\nImplementation:\n"+(a.app/"app.py").read_text()
                +"\nTests:\n"+(a.app/"test_app.py").read_text()
                +"\nExecuted checks:\n"+json.dumps(result,sort_keys=True))
        response=model_rpc(a.model_socket,a.actor,key,f"{oid}:qa-review-{rnd}",f"REVIEW{rnd}",prompt)
        verdict=response.get("result",{})
        if verdict.get("verdict") not in ("PASS","REJECTED"):
            raise ValueError("invalid independent QA verdict")
        result.update({"model_verdict":verdict,"model_receipt":response.get("receipt")})
        a.qa.mkdir(parents=True,exist_ok=True);ev=a.qa/f"qa_round{rnd}.json"
        ev.write_text(json.dumps(result,sort_keys=True,indent=2))
        evidence={"round":rnd,"artifact_sha256":result["artifact_sha256"],"test_sha256":result["test_sha256"],
                  "qa_evidence_sha256":digest(ev),"qa_evidence":str(ev),
                  "local_test_exit":result["local_test"]["exit_code"],"probe_exit":result["probe"]["exit_code"]}
        if qa_pass(result,verdict):
            send("MORCH","QA_PASS",{**evidence,"verdict":"PASS"},f"qa-pass-{rnd}")
        else:
            reason=str(verdict.get("reason","QA rejected"))[:2000]
            if not checks_pass(result):reason+="; required executed checks failed: "+json.dumps(result,sort_keys=True)[:6000]
            send("RORCH","QA_REJECTED",{**evidence,"reason":reason},f"qa-rejected-{rnd}")
    elif a.actor=="MORCH" and kind=="QA_PASS":
        rnd=int(payload.get("round",0))
        if rnd not in (1,2) or payload.get("verdict")!="PASS":raise ValueError("invalid QA acceptance")
        ev=a.qa/f"qa_round{rnd}.json";result=json.loads(ev.read_text())
        if not qa_pass(result,result.get("model_verdict",{})):raise ValueError("QA evidence does not pass")
        if digest(ev)!=payload.get("qa_evidence_sha256"):raise ValueError("QA evidence hash mismatch")
        if digest(a.app/"app.py")!=payload.get("artifact_sha256") or digest(a.app/"test_app.py")!=payload.get("test_sha256"):
            raise ValueError("accepted artifact hash mismatch")
        send("RORCH","ACCEPTED",{**payload,"decision":"ACCEPTED"},"accepted")
    elif a.actor=="RORCH" and kind=="ACCEPTED":
        if payload.get("decision")!="ACCEPTED":raise ValueError("invalid acceptance")
        if digest(a.app/"app.py")!=payload.get("artifact_sha256") or digest(a.app/"test_app.py")!=payload.get("test_sha256"):
            raise ValueError("ack artifact hash mismatch")
        send("MORCH","RORCH_ACK",{**payload,"decision":"ACKNOWLEDGED"},"rorch-ack")
    elif a.actor=="MORCH" and kind=="RORCH_ACK":
        if payload.get("decision")!="ACKNOWLEDGED":raise ValueError("invalid final acknowledgement")
        return {**payload,"terminal_metadata":True}
    else:
        raise ValueError("unsupported P2 actor/message")
    return base
