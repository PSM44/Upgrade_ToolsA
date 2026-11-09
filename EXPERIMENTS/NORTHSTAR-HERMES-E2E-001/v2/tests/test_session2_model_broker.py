import json, os, sqlite3, stat, sys, tempfile, unittest
from pathlib import Path

SRC=Path(__file__).resolve().parents[1]/"src";sys.path.insert(0,str(SRC))
from model_broker import Broker
from operational_memory import OperationalMemory


class ModelBrokerTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True);root=Path(self.tmp.name)
        keys=root/"keys";keys.mkdir()
        for actor in ("morch","rorch","qa"):(keys/f"{actor}.key").write_bytes(actor.encode()*16)
        routes=root/"routes.json";routes.write_text(json.dumps({"roles":{
            "MORCH":{"provider":"custom","model":"coord"},
            "RORCH":{"provider":"custom","model":"dev"},
            "QA":{"provider":"custom","model":"review"}}}),encoding="utf-8")
        fake=root/"hermes";fake.write_text("""#!/usr/bin/env python3
import json,sys
u=sys.argv[sys.argv.index('--usage-file')+1]
json.dump({'provider':'custom','model':sys.argv[sys.argv.index('--model')+1],'session_id':'fake-session','input_tokens':7,'output_tokens':3,'total_tokens':10,'cost_status':'unknown','cost_source':'none'},open(u,'w'))
print(json.dumps({'files':{'app.py':'x','test_app.py':'y'}}))
""",encoding="utf-8");fake.chmod(fake.stat().st_mode|stat.S_IEXEC)
        self.broker=Broker(root/"calls.db",keys,root/"work",routes,f'{sys.executable} {fake}');self.root=root
    def tearDown(self):self.tmp.cleanup()
    def req(self,actor="RORCH",stage="BUILD",rid="one",prompt="make it"):
        return {"op":"call","actor":actor,"key":self.broker.keys[actor].hex(),"request_id":rid,"stage":stage,"prompt":prompt}
    def test_authenticated_acl_idempotent_receipt_unknown_cost(self):
        first=self.broker.handle(self.req());second=self.broker.handle(self.req())
        self.assertFalse(first["duplicate"]);self.assertTrue(second["duplicate"])
        self.assertEqual(first["receipt"]["actual_model"],"dev")
        self.assertEqual(first["receipt"]["cost_status"],"unknown");self.assertIsNone(first["receipt"]["cost_value"])
        with self.assertRaises(PermissionError):self.broker.handle(self.req(stage="REVIEW1",rid="bad"))
        forged=self.req(rid="forged");forged["key"]="00"*16
        with self.assertRaises(PermissionError):self.broker.handle(forged)
        dump=(self.root/"calls.db").read_bytes()
        self.assertNotIn(self.broker.keys["RORCH"].hex().encode(),dump)


class CrossObjectiveMemoryTest(unittest.TestCase):
    def test_cross_objective_retrieval_survives_restart(self):
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"memory.db"
            with OperationalMemory(db) as m:
                m.append(task_id="OLD",kind="DECISION",actor="MORCH_LOCAL_ADAPTER",payload={"decision":"preserve punctuation normalization"})
            with OperationalMemory(db) as m:
                found=m.search_all("punctuation normalization",kinds=["DECISION"])
                self.assertEqual(found[0]["task_id"],"OLD");self.assertTrue(m.verify_chain("OLD"))


if __name__=="__main__":unittest.main()

