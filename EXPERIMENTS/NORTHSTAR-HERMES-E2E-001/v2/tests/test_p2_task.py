import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SRC=Path(__file__).resolve().parents[1]/"src"
sys.path.insert(0,str(SRC))
import p2_task as task
from morch_delegate import Delegate,signed

class P2ContractTest(unittest.TestCase):
    def test_safe_identifiers_and_spec_fingerprint(self):
        self.assertEqual(task.safe_objective_id("NORTHSTAR-P2_001"),"NORTHSTAR-P2_001")
        for value in ("../escape","/absolute",".","", "x/y","x"*97,True):
            with self.subTest(value=value),self.assertRaises(ValueError):
                task.safe_objective_id(value)
        payload={"objective_id":"P2","fixture_spec":task.SPEC_ID,"fixture_sha256":task.SPEC_SHA256}
        task.validate_fixture(payload)
        with self.assertRaises(ValueError):
            task.validate_fixture(dict(payload,fixture_sha256="wrong"))
    def test_generated_checks_have_no_actor_authority_mount(self):
        command=task.isolated_command(Path("/role/app"),["/usr/bin/python3","-c","pass"])
        self.assertIn("--unshare-all",command)
        self.assertIn("--clearenv",command)
        self.assertNotIn("/identity/key.bin",command)
        self.assertNotIn("/ipc",command)
        self.assertNotIn("/code",command)
        self.assertEqual(command[command.index("--ro-bind",command.index("/lib64"))+1],"/role/app")
    def test_independent_qa_can_reject_passing_checks(self):
        result={"local_test":{"exit_code":0},"probe":{"exit_code":0},"unchanged":True,"tests_extended":True,"test_count":4,"probe_completed":True}
        self.assertFalse(task.qa_pass(result,{"verdict":"REJECTED"}))
        self.assertTrue(task.qa_pass(result,{"verdict":"PASS"}))
        result["probe"]["exit_code"]=1
        self.assertFalse(task.qa_pass(result,{"verdict":"PASS"}))
    def test_no_acceptance_for_missing_probe_marker_or_zero_tests(self):
        result={"local_test":{"exit_code":0},"probe":{"exit_code":0},"unchanged":True,
                "tests_extended":True,"test_count":4,"probe_completed":True}
        self.assertTrue(task.checks_pass(result))
        self.assertFalse(task.checks_pass(dict(result,probe_completed=False)))
        self.assertFalse(task.checks_pass(dict(result,test_count=0)))
    def test_independent_probe_catches_cap_and_validation_bugs(self):
        good='''import math
def retry_delays(attempts, initial=1.0, factor=2.0, cap=30.0):
    if isinstance(attempts,bool) or not isinstance(attempts,int) or not 0<=attempts<=8:
        raise ValueError()
    for value in (initial,factor,cap):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise ValueError()
    if factor<1: raise ValueError()
    result=[]
    current=min(initial,cap)
    for _ in range(attempts):
        result.append(current)
        current=min(current*factor,cap)
    return result
'''
        # Execute trusted test fixtures only; generated runtime code uses bwrap.
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            app=Path(td)/"app.py";app.write_text(good)
            cmd=[sys.executable,"-B","-c",task.PROBE]
            p=subprocess.run(cmd,cwd=td,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            app.write_text(good.replace("current=min(current*factor,cap)","current=current*factor"))
            self.assertNotEqual(subprocess.run(cmd,cwd=td,capture_output=True).returncode,0)
            app.write_text(good.replace("isinstance(attempts,bool) or ",""))
            self.assertNotEqual(subprocess.run(cmd,cwd=td,capture_output=True).returncode,0)
    def test_scaffold_has_no_solution(self):
        scope={}
        exec(task.SCAFFOLD,scope)
        with self.assertRaises(NotImplementedError):scope["retry_delays"](2)

class P2DelegateTest(unittest.TestCase):
    def test_fixture_is_signed_propagated_and_part_of_replay_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);keys=root/"keys";keys.mkdir();(keys/"morch.key").write_bytes(b"test"*8)
            control=root/"control.db"
            with sqlite3.connect(control) as c:c.execute("CREATE TABLE objectives(objective_id text,state text,result_json text)")
            d=Delegate(root/"delegate.db",keys,"fake",control)
            fields={"operation":"submit","objective_id":"P2","objective":"build retry delays","nonce":"unique","fixture_spec":task.SPEC_ID}
            with patch("morch_delegate.transport_rpc",return_value={"ok":True,"state":"PENDING"}) as rpc:
                self.assertFalse(d.handle(signed(d.human_key,fields))["duplicate"])
                self.assertTrue(d.handle(signed(d.human_key,fields))["duplicate"])
                self.assertEqual(rpc.call_args.args[-1],task.SPEC_ID)
                with self.assertRaises(RuntimeError):
                    d.handle(signed(d.human_key,dict(fields,fixture_spec="palindrome_v1")))
                with self.assertRaises(ValueError):
                    d.handle(signed(d.human_key,dict(fields,objective_id="../bad")))
                with self.assertRaises(ValueError):
                    d.handle(signed(d.human_key,dict(fields,objective_id="P3",fixture_spec="unknown")))
            failed={"objective_id":"P2","status":"FAILED","reason":"bounded rejection"}
            with sqlite3.connect(control) as c:c.execute("INSERT INTO objectives VALUES(?,?,?)",("P2","FAILED",json.dumps(failed)))
            result=d.handle(signed(d.human_key,{"operation":"result","objective_id":"P2"}))
            self.assertTrue(result["ready"]);self.assertEqual(result["result"],failed)

class P2FlowTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.a=SimpleNamespace(actor="RORCH",socket="transport",model_socket="broker",app=root/"app",dev=root/"dev",qa=root/"qa")
        for p in (self.a.app,self.a.dev,self.a.qa):p.mkdir()
        (self.a.app/"app.py").write_text("artifact")
        (self.a.app/"test_app.py").write_text("tests")
        self.base={"objective_id":"P2","fixture_spec":task.SPEC_ID,"fixture_sha256":task.SPEC_SHA256,"objective":"bounded objective"}
        self.emitted=[]
    def tearDown(self):self.temp.cleanup()
    def emit(self,*args):self.emitted.append(args)
    def execute(self,kind,payload,model):
        return task.handle(self.a,{"payload":{**self.base,**payload},"message_type":kind},b"test",None,model,self.emit)
    def passing(self):
        return {"local_test":{"exit_code":0},"probe":{"exit_code":0},"unchanged":True,"tests_extended":True,"test_count":4,"probe_completed":True,
                "artifact_sha256":task.digest(self.a.app/"app.py"),"test_sha256":task.digest(self.a.app/"test_app.py")}
    def test_real_edit_rpc_and_fixture_propagation(self):
        calls=[]
        def model(*args,**kwargs):
            calls.append((args,kwargs));return {"result":{"edited":True},"receipt":{"runtime":"opencode"}}
        with patch("p2_task.checks",return_value=self.passing()):
            result=self.execute("TASK_HANDOFF",{},model)
        self.assertEqual(calls[0][1],{"op":"edit","objective_id":"P2"})
        self.assertEqual(calls[0][0][3:5],("P2:rorch-build","BUILD"))
        emitted=self.emitted[-1]
        self.assertEqual(emitted[4],"DELIVERY")
        self.assertEqual(emitted[6]["fixture_sha256"],task.SPEC_SHA256)
        self.assertFalse(result.get("terminal_failure",False))
    def test_qa_rejection_is_not_overridden_and_max_rounds_terminate(self):
        self.a.actor="QA";checks=self.passing()
        model=lambda *args,**kwargs:{"result":{"verdict":"REJECTED","reason":"independent contract finding"},"receipt":{}}
        with patch("p2_task.checks",return_value=checks):
            self.execute("REWORK_RESPONSE",{"round":2,"artifact_sha256":checks["artifact_sha256"],"test_sha256":checks["test_sha256"]},model)
        edge=self.emitted[-1];self.assertEqual(edge[4],"QA_REJECTED")
        self.a.actor="RORCH"
        result=self.execute("QA_REJECTED",edge[6],lambda *a,**k:self.fail("third model round"))
        self.assertTrue(result["terminal_failure"]);self.assertEqual(result["round"],2)
    def test_acceptance_and_ack_carry_verified_evidence(self):
        self.a.actor="QA";checks=self.passing()
        model=lambda *args,**kwargs:{"result":{"verdict":"PASS","reason":"reviewed"},"receipt":{}}
        with patch("p2_task.checks",return_value=checks):
            self.execute("DELIVERY",{"round":1,"artifact_sha256":checks["artifact_sha256"],"test_sha256":checks["test_sha256"]},model)
        payload=self.emitted[-1][6]
        self.a.actor="MORCH";self.execute("QA_PASS",payload,model)
        payload=self.emitted[-1][6]
        self.a.actor="RORCH";self.execute("ACCEPTED",payload,model)
        payload=self.emitted[-1][6]
        self.a.actor="MORCH";result=self.execute("RORCH_ACK",payload,model)
        self.assertTrue(result["terminal_metadata"])
        self.assertEqual(result["qa_evidence_sha256"],task.digest(self.a.qa/"qa_round1.json"))
        (self.a.app/"app.py").write_text("changed")
        with self.assertRaises(ValueError):self.execute("QA_PASS",dict(payload,verdict="PASS"),model)

if __name__=="__main__":unittest.main()
