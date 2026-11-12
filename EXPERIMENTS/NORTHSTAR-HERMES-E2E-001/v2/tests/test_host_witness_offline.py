"""Offline TEST-key protocol negatives; no provider, ETW or privileged operations."""
import copy
import importlib.util
import json
import secrets
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from host_witness_offline import (TestKey, OfflineLedger, SimulatedWindowsWitness,
    OfflineRunnerAdapter, Rejected, canonical, parse)

class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="witness-test-")
        cls.keys = {name:TestKey(cls.tmp.name,name) for name in ("W","D")}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.dbtmp = tempfile.TemporaryDirectory(dir=self.tmp.name)
        self.path = Path(self.dbtmp.name)/"custody.db"
        self.public = {n:k.public for n,k in self.keys.items()}
        self.ledger = OfflineLedger(self.path,self.public)
        self.identity = dict(run_id=secrets.token_hex(16),nonce=secrets.token_hex(32),
            objective="offline",runtime="/home/psm/northstar/state/NORTHSTAR_P2_TEST",
            manifest="a"*64,session="session1",host_boot="winboot",distro_boot="boot",
            launch=dict(pid=100,start_ticks=123,uid=1000,exe_sha256="b"*64,
                        argv_sha256="c"*64,unit="unit1"))
        self.records = SimulatedWindowsWitness(self.keys).transcript(self.identity)

    def tearDown(self):
        self.ledger.close()
        self.dbtmp.cleanup()

    def feed(self, records=None):
        for idx, r in enumerate(self.records if records is None else records):
            self.ledger.accept(r,100+idx)

    def altered(self, idx, change):
        body=copy.deepcopy(parse(self.records[idx])["body"])
        change(body)
        return self.keys[body["signer"]].sign(body)

    def test_positive_and_restart_custody(self):
        self.feed()
        self.ledger.close()
        self.ledger=OfflineLedger(self.path,self.public)
        out=self.ledger.audit(self.identity["run_id"],self.records)
        self.assertFalse(out["execution_authorized"])
        self.assertEqual(set(out["gates"].values()),{"OPEN"})

    def test_all_prefixes_and_initial_middle_suffix_truncation(self):
        self.feed()
        variants=[self.records[:n] for n in range(7)]
        variants += [self.records[1:],self.records[:3]+self.records[4:],
                     self.records[:-1]+[self.records[-1]+b"\n"]]
        for data in variants:
            with self.subTest(size=len(data)), self.assertRaises(Rejected):
                self.ledger.audit(self.identity["run_id"],data)

    def test_replay_and_concurrent_reservation(self):
        self.ledger.accept(self.records[0],100)
        with self.assertRaises(Rejected):
            self.ledger.accept(self.records[0],100)
        other=copy.deepcopy(self.identity)
        other.update(run_id=secrets.token_hex(16),nonce=secrets.token_hex(32))
        with self.assertRaises(Rejected):
            self.ledger.accept(SimulatedWindowsWitness(self.keys).transcript(other)[0],100)

    def test_out_of_order_and_direct_execute(self):
        for idx in range(1,7):
            with self.assertRaises(Rejected):
                self.ledger.accept(self.records[idx],100+idx)

    def test_every_identity_field_substitution(self):
        self.ledger.accept(self.records[0],100)
        for field in self.identity:
            def change(b):
                if field=="launch": b["identity"][field]["pid"]+=1
                else: b["identity"][field]+="0"
            with self.subTest(field=field),self.assertRaises(Rejected):
                self.ledger.accept(self.altered(1,change),101)

    def test_coverage_faults_and_polling_only(self):
        self.ledger.accept(self.records[0],100)
        for field,value in (("source","POLLING"),("loss",1),("unknown",1),
                            ("forbidden",1),("continuous",False)):
            with self.subTest(field=field),self.assertRaises(Rejected):
                self.ledger.accept(self.altered(1,lambda b:b["payload"].update({field:value})),101)

    def test_terminal_loss_late_event_and_no_drain(self):
        self.feed(self.records[:6])
        for field,value in (("loss",1),("forbidden",1),("drained",False),
                            ("begin","0"*64),("quiescent",False),("count",6),
                            ("challenge","0"*64)):
            with self.subTest(field=field),self.assertRaises(Rejected):
                self.ledger.accept(self.altered(6,lambda b:b["payload"].update({field:value})),106)

    def test_clock_deadline_and_sequence(self):
        self.ledger.accept(self.records[0],100)
        for field,value in (("time",99),("time",102),("deadline",1000),
                            ("sequence",True),("sequence",3),("previous","f"*64)):
            with self.subTest(field=field),self.assertRaises(Rejected):
                self.ledger.accept(self.altered(1,lambda b:b.update({field:value})),101)
        with self.assertRaises(Rejected):
            self.ledger.accept(self.records[1],131)

    def test_signature_alteration_and_wrong_key(self):
        r=parse(self.records[0])
        r["signature"]=r["signature"][:-4]+"AAAA"
        with self.assertRaises(Rejected): self.ledger.accept(canonical(r),100)
        with self.assertRaises(Rejected):
            self.ledger.accept(self.keys["W"].sign(parse(self.records[0])["body"]),100)

    def test_schema_and_real_evidence_rejected(self):
        for field,value in (("schema","other"),("simulation",False),("kind","STATUS"),
                            ("key_id","0"*64)):
            raw=parse(self.records[0]); raw["body"][field]=value
            with self.assertRaises(Rejected):self.ledger.accept(canonical(raw),100)
        with self.assertRaises(Rejected):canonical({"n":1.0})
        for raw in (b'{"a":1,"a":2}',b'{ "a":1}',b'{"n":NaN}',b'{"n":1.0}'):
            with self.assertRaises(Rejected):parse(raw)
        self.assertEqual(parse(canonical({"n":9007199254740993,"text":"ñ"}))["n"],
                         9007199254740993)

    def test_failed_transition_atomic_and_missing_custody(self):
        self.ledger.accept(self.records[0],100)
        with self.assertRaises(Rejected):
            self.ledger.accept(self.altered(1,lambda b:b["payload"].update(loss=1)),101)
        for idx in range(1,7):self.ledger.accept(self.records[idx],100+idx)
        with OfflineDummy(self.public,self.dbtmp.name) as empty:
            with self.assertRaises(Rejected):empty.audit(self.identity["run_id"],self.records)

    def test_runner_adapter_never_authorizes(self):
        spec=importlib.util.spec_from_file_location("runner",
                Path(__file__).resolve().parents[1]/"tools/p2_external.py")
        runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
        adapter=OfflineRunnerAdapter(runner.GateBlocked)
        for phase in ("before_arm","before_create","heartbeat","after_quiescence"):
            with self.assertRaises(runner.GateBlocked):
                adapter.verify(Path(self.identity["runtime"]),"offline",phase)
        with self.assertRaises(runner.GateBlocked):
            runner.IndependentWitness().verify(Path(self.identity["runtime"]),"offline","before_arm")


    def test_terminal_challenge_consumption_and_replay(self):
        self.feed()
        challenge=self.ledger.challenge_terminal(self.identity["run_id"])
        body=dict(kind="STATUS",simulation=True,run_id=self.identity["run_id"],
                  nonce=self.identity["nonce"],close=parse(self.records[-1])["sha256"],
                  challenge=challenge,time=107,key_id=self.keys["W"].key_id)
        wrong=copy.deepcopy(body);wrong["challenge"]="0"*64
        with self.assertRaises(Rejected):
            self.ledger.consume_terminal(self.identity["run_id"],self.records,
                                         self.keys["W"].sign(wrong),107)
        signed=self.keys["W"].sign(body)
        out=self.ledger.consume_terminal(self.identity["run_id"],self.records,signed,107)
        self.assertFalse(out["execution_authorized"])
        with self.assertRaises(Rejected):
            self.ledger.consume_terminal(self.identity["run_id"],self.records,signed,107)
        with self.assertRaises(Rejected):
            self.ledger.challenge_terminal(self.identity["run_id"])



    def test_durable_abort_on_simulated_source_failure(self):
        self.feed(self.records[:4])
        self.ledger.abort(self.identity["run_id"])
        self.ledger.close()
        self.ledger=OfflineLedger(self.path,self.public)
        with self.assertRaises(Rejected):self.ledger.accept(self.records[4],104)
        with self.assertRaises(Rejected):self.ledger.audit(self.identity["run_id"],self.records)
        with self.assertRaises(Rejected):self.ledger.accept(self.records[0],100)
        fresh=copy.deepcopy(self.identity)
        fresh.update(run_id=secrets.token_hex(16),nonce=secrets.token_hex(32),session="new-session")
        self.ledger.accept(SimulatedWindowsWitness(self.keys).transcript(fresh)[0],100)

    def test_scheduler_test_xml_no_triggers(self):
        import xml.etree.ElementTree as ET
        from host_witness_offline import scheduler_xml
        root=ET.fromstring(scheduler_xml("C:\\TEST\\pwsh.exe","TEST_ONLY"))
        ns={"t":"http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(len(root.find("t:Triggers",ns)),0)
        self.assertEqual(root.find("t:Settings/t:AllowStartOnDemand",ns).text,"true")
        self.assertEqual(root.find("t:Settings/t:MultipleInstancesPolicy",ns).text,"IgnoreNew")
        self.assertEqual(root.find("t:Actions/t:Exec/t:Arguments",ns).text,
                         "-NoProfile -NonInteractive -Command exit")


class OfflineDummy:
    def __init__(self,keys,directory):
        self.ledger=OfflineLedger(Path(directory)/"empty.db",keys)
    def __enter__(self): return self.ledger
    def __exit__(self,*args):self.ledger.close()

if __name__=="__main__":unittest.main()
