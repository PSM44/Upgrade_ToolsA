import json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

SRC=Path(__file__).resolve().parents[1]/"src";sys.path.insert(0,str(SRC))
from morch_delegate import Delegate,signed

class MorchDelegateTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True);root=Path(self.tmp.name);keys=root/"keys";keys.mkdir()
  (keys/"morch.key").write_bytes(b"m"*32);self.control=root/"control.db"
  with sqlite3.connect(self.control) as c:c.execute("create table objectives(objective_id text primary key,state text,result_json text)")
  self.delegate=Delegate(root/"delegate.db",keys,"/fake/transport.sock",self.control);self.key=self.delegate.human_key
 def tearDown(self):self.tmp.cleanup()
 def test_authenticated_ingress_replay_and_terminal_egress(self):
  fields={"operation":"submit","objective_id":"O1","objective":"build it","nonce":"n1"}
  with patch("morch_delegate.transport_rpc",return_value={"ok":True,"state":"PENDING"}) as rpc:
   first=self.delegate.handle(signed(self.key,fields));duplicate=self.delegate.handle(signed(self.key,fields))
  self.assertFalse(first["duplicate"]);self.assertTrue(duplicate["duplicate"]);rpc.assert_called_once()
  conflict=dict(fields,objective="changed")
  with self.assertRaises(RuntimeError):self.delegate.handle(signed(self.key,conflict))
  forged=signed(b"x"*32,{"operation":"result","objective_id":"O1"})
  with self.assertRaises(PermissionError):self.delegate.handle(forged)
  result={"objective_id":"O1","status":"ACCEPTED","human_relay_count":0}
  with sqlite3.connect(self.control) as c:c.execute("insert into objectives values(?,?,?)",("O1","COMPLETED",json.dumps(result)))
  received=self.delegate.handle(signed(self.key,{"operation":"result","objective_id":"O1"}))
  self.assertTrue(received["ready"]);self.assertEqual(received["authority"],"MORCH_LOCAL_DELEGATE");self.assertEqual(received["result"],result)

if __name__=="__main__":unittest.main()

