import json,sys,tempfile,unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from model_broker import Broker


class DirectOmniRouteBrokerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name);keys=root/"keys";keys.mkdir()
  for actor in ("MORCH","RORCH","QA"):(keys/f"{actor.lower()}.key").write_bytes((actor*8).encode())
  fake=root/"fake_client.py";fake.write_text('''import json,sys
r=json.load(sys.stdin)
if "EMPTY" in r["messages"][-1]["content"]:
 content=None
else: content='{"verdict":"PASS"}'
print(json.dumps({"ok":True,"status":200,"data":{"id":"gen-test","model":"vendor/model-b:free","choices":[{"finish_reason":"stop","message":{"content":content}}],"usage":{"prompt_tokens":11,"completion_tokens":3,"total_tokens":14,"cost":0}}}))
''',encoding="utf-8")
  routes=root/"routes.json";routes.write_text(json.dumps({"roles":{a:{"runtime":"omniroute","provider":"openrouter","model":"openrouter/vendor/model-b:free","node":sys.executable,"client":str(fake)} for a in ("MORCH","RORCH","QA")}}),encoding="utf-8")
  self.broker=Broker(root/"calls.db",keys,root/"work",routes);self.root=root
 def tearDown(self):self.tmp.cleanup()
 def request(self,rid="r1",prompt="valid"):
  return {"op":"call","actor":"QA","key":self.broker.keys["QA"].hex(),"request_id":rid,"stage":"REVIEW1","prompt":prompt}
 def test_records_actual_model_usage_cost_and_deduplicates(self):
  first=self.broker.handle(self.request());second=self.broker.handle(self.request())
  self.assertFalse(first["duplicate"]);self.assertTrue(second["duplicate"])
  receipt=first["receipt"];self.assertEqual("vendor/model-b:free",receipt["actual_model"])
  self.assertEqual(14,receipt["total_tokens"]);self.assertEqual("known",receipt["cost_status"]);self.assertEqual(0,receipt["cost_value"])
 def test_empty_final_content_fails_closed(self):
  with self.assertRaisesRegex(RuntimeError,"no final content"):self.broker.handle(self.request("empty","EMPTY"))

if __name__=="__main__":unittest.main()
