#!/usr/bin/env python3
"""Authenticated HUMAN ingress/egress for MORCH_LOCAL_DELEGATE (not ChatGPT)."""
from __future__ import annotations
import argparse,hashlib,hmac,json,os,socket,sqlite3,time
from pathlib import Path
from typing import Any

SCHEMA="""PRAGMA journal_mode=DELETE;
CREATE TABLE IF NOT EXISTS requests(objective_id TEXT PRIMARY KEY,nonce TEXT NOT NULL UNIQUE,objective_sha256 TEXT NOT NULL,status TEXT NOT NULL,transport_state TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL,result_json TEXT);
"""
def canonical(v:dict[str,Any])->bytes:return json.dumps(v,sort_keys=True,separators=(",",":")).encode()
def send(s:socket.socket,v:dict[str,Any])->None:s.sendall(canonical(v)+b"\n")
def recv(s:socket.socket)->dict[str,Any]:
 data=b""
 while not data.endswith(b"\n"):
  part=s.recv(65536)
  if not part:break
  data+=part
 return json.loads(data.decode())
def transport_rpc(path:str,key:bytes,objective_id:str,objective:str)->dict[str,Any]:
 mid=f"{objective_id}:task-handoff";request={"op":"send","actor":"MORCH","key":key.hex(),"recipient":"RORCH","message_type":"TASK_HANDOFF","message_id":mid,"nonce":hashlib.sha256(mid.encode()).hexdigest(),"payload":{"objective_id":objective_id,"objective":objective,"fixture_spec":"palindrome_v1","authority":"MORCH_LOCAL_DELEGATE"}}
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:s.connect(path);send(s,request);return recv(s)

class Delegate:
 def __init__(self,db:Path,key_dir:Path,transport_socket:str,control_db:Path):
  self.db=db;self.transport_socket=transport_socket;self.control_db=control_db;self.running=True
  human=key_dir/"human.key"
  if not human.exists():human.write_bytes(os.urandom(32))
  self.human_key=human.read_bytes();self.morch_key=(key_dir/"morch.key").read_bytes()
  with sqlite3.connect(db) as c:c.executescript(SCHEMA)
 def auth(self,r:dict[str,Any])->None:
  supplied=bytes.fromhex(str(r.get("key","")));unsigned={k:v for k,v in r.items() if k not in {"key","signature"}}
  expected=hmac.new(self.human_key,canonical(unsigned),hashlib.sha256).hexdigest()
  if not hmac.compare_digest(supplied,self.human_key) or not hmac.compare_digest(str(r.get("signature","")),expected):raise PermissionError("HUMAN capability/signature mismatch")
 def terminal(self,oid:str)->dict[str,Any]|None:
  if not self.control_db.exists():return None
  with sqlite3.connect(f"file:{self.control_db.as_posix()}?mode=ro",uri=True) as c:row=c.execute("SELECT state,result_json FROM objectives WHERE objective_id=?",(oid,)).fetchone()
  return json.loads(row[1]) if row and row[0]=="COMPLETED" and row[1] else None
 def handle(self,r:dict[str,Any])->dict[str,Any]:
  self.auth(r);op=str(r.get("operation"))
  if op=="shutdown":self.running=False;return {"ok":True,"authority":"MORCH_LOCAL_DELEGATE"}
  oid=str(r["objective_id"])
  if op=="submit":
   objective=str(r["objective"]);nonce=str(r["nonce"]);digest=hashlib.sha256(objective.encode()).hexdigest();now=time.time()
   with sqlite3.connect(self.db) as c:
    prior=c.execute("SELECT nonce,objective_sha256,transport_state FROM requests WHERE objective_id=?",(oid,)).fetchone()
    if prior:
     if prior[0]!=nonce or prior[1]!=digest:raise RuntimeError("objective replay conflict")
     return {"ok":True,"duplicate":True,"objective_id":oid,"transport_state":prior[2],"authority":"MORCH_LOCAL_DELEGATE"}
    if c.execute("SELECT 1 FROM requests WHERE nonce=?",(nonce,)).fetchone():raise RuntimeError("nonce replay")
    response=transport_rpc(self.transport_socket,self.morch_key,oid,objective)
    if not response.get("ok"):raise RuntimeError(f"transport rejected objective: {response}")
    c.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,?,NULL)",(oid,nonce,digest,"ACTIVE",response.get("state"),now,now))
   return {"ok":True,"duplicate":False,"objective_id":oid,"transport_state":response.get("state"),"authority":"MORCH_LOCAL_DELEGATE"}
  if op in {"result","wait"}:
   deadline=time.time()+float(r.get("timeout",0) if op=="wait" else 0)
   while True:
    result=self.terminal(oid)
    if result:
     with sqlite3.connect(self.db) as c:c.execute("UPDATE requests SET status='COMPLETED',updated_at=?,result_json=? WHERE objective_id=?",(time.time(),json.dumps(result,sort_keys=True),oid))
     return {"ok":True,"ready":True,"objective_id":oid,"result":result,"authority":"MORCH_LOCAL_DELEGATE"}
    if time.time()>=deadline:return {"ok":True,"ready":False,"objective_id":oid,"authority":"MORCH_LOCAL_DELEGATE"}
    time.sleep(.25)
  raise ValueError("unsupported delegate operation")

def signed(key:bytes,fields:dict[str,Any])->dict[str,Any]:
 req=dict(fields);req["key"]=key.hex();req["signature"]=hmac.new(key,canonical(fields),hashlib.sha256).hexdigest();return req
def serve(a:argparse.Namespace)->int:
 path=Path(a.socket);path.parent.mkdir(parents=True,exist_ok=True);path.parent.chmod(0o700);path.unlink(missing_ok=True);delegate=Delegate(Path(a.db),Path(a.key_dir),a.transport_socket,Path(a.control_db))
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
  listener.bind(str(path));path.chmod(0o600);listener.listen(8);listener.settimeout(1)
  while delegate.running:
   try:conn,_=listener.accept()
   except TimeoutError:continue
   with conn:
    try:response=delegate.handle(recv(conn))
    except Exception as exc:response={"ok":False,"error":type(exc).__name__,"detail":str(exc)}
    send(conn,response)
 path.unlink(missing_ok=True);return 0
def client(a:argparse.Namespace)->int:
 key=a.key.read_bytes();fields={"operation":a.operation}
 if a.objective_id:fields["objective_id"]=a.objective_id
 if a.objective is not None:fields["objective"]=a.objective
 if a.nonce:fields["nonce"]=a.nonce
 if a.timeout is not None:fields["timeout"]=a.timeout
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:s.connect(a.socket);send(s,signed(key,fields));response=recv(s)
 print(json.dumps(response,sort_keys=True));return 0 if response.get("ok") else 3
def main()->int:
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest="command",required=True)
 s=sub.add_parser("serve");s.add_argument("--socket",required=True);s.add_argument("--db",required=True);s.add_argument("--transport-socket",required=True);s.add_argument("--control-db",required=True);s.add_argument("--key-dir",required=True)
 c=sub.add_parser("client");c.add_argument("--socket",required=True);c.add_argument("--key",required=True,type=Path);c.add_argument("--operation",required=True,choices=("submit","result","wait","shutdown"));c.add_argument("--objective-id");c.add_argument("--objective");c.add_argument("--nonce");c.add_argument("--timeout",type=float)
 a=p.parse_args();return serve(a) if a.command=="serve" else client(a)
if __name__=="__main__":raise SystemExit(main())

