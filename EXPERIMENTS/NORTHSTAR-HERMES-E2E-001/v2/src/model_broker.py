#!/usr/bin/env python3
"""Authenticated secret-zero broker for real Hermes one-shot calls.

The isolated actor sees one capability and a Unix socket.  Only this broker,
outside the actor guest, invokes the existing Hermes installation and its
credential-bearing configuration.  Receipts contain no credential values.
"""
from __future__ import annotations
import argparse, hashlib, hmac, json, os, re, shlex, socket, sqlite3, subprocess, time
from pathlib import Path
from typing import Any

HERMES="/home/aazcl/.local/bin/hermes"
ALLOWED={"RORCH":{"BUILD","FIX"},"QA":{"REVIEW1","REVIEW2"},"MORCH":{"DECISION"}}
SCHEMA="""PRAGMA journal_mode=DELETE;
CREATE TABLE IF NOT EXISTS calls(request_id TEXT PRIMARY KEY,actor TEXT NOT NULL,stage TEXT NOT NULL,requested_model TEXT NOT NULL,actual_provider TEXT,actual_model TEXT,session_id TEXT,status TEXT NOT NULL,input_sha256 TEXT NOT NULL,output_sha256 TEXT,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,cost_value REAL,cost_status TEXT,cost_source TEXT,exit_code INTEGER,created_at REAL NOT NULL,completed_at REAL,response_json TEXT,usage_json TEXT,error TEXT);
"""

def send(sock:socket.socket,value:dict[str,Any])->None:sock.sendall(json.dumps(value,sort_keys=True,separators=(",",":")).encode()+b"\n")
def recv(sock:socket.socket)->dict[str,Any]:
 data=b""
 while not data.endswith(b"\n"):
  part=sock.recv(65536)
  if not part:break
  data+=part
 return json.loads(data.decode())
def json_object(text:str)->dict[str,Any]:
 decoder=json.JSONDecoder();matches=list(re.finditer(r"\{",text))
 for match in matches:
  try:value,_=decoder.raw_decode(text[match.start():])
  except json.JSONDecodeError:continue
  if isinstance(value,dict):return value
 raise ValueError("Hermes response contained no JSON object")

class Broker:
 def __init__(self,db:Path,key_dir:Path,work_root:Path,routes:Path,hermes:str=HERMES):
  self.db=db;self.key_dir=key_dir;self.work_root=work_root;self.routes=json.loads(routes.read_text(encoding="utf-8"));self.running=True
  self.hermes=hermes
  self.keys={a:(key_dir/f"{a.lower()}.key").read_bytes() for a in ALLOWED}
  work_root.mkdir(parents=True,exist_ok=True)
  with sqlite3.connect(db) as c:c.executescript(SCHEMA)
 def auth(self,r:dict[str,Any])->tuple[str,bytes]:
  actor=str(r.get("actor",""));supplied=bytes.fromhex(str(r.get("key","")))
  if actor not in self.keys or not hmac.compare_digest(supplied,self.keys[actor]):raise PermissionError("model capability mismatch")
  return actor,supplied
 def call(self,actor:str,r:dict[str,Any])->dict[str,Any]:
  stage=str(r["stage"]);request_id=str(r["request_id"]);prompt=str(r["prompt"])
  if stage not in ALLOWED[actor]:raise PermissionError("actor/stage route denied")
  route=self.routes["roles"][actor];model=str(route["model"]);provider=str(route.get("provider","custom"))
  with sqlite3.connect(self.db) as c:
   c.row_factory=sqlite3.Row;prior=c.execute("SELECT * FROM calls WHERE request_id=?",(request_id,)).fetchone()
   if prior:
    if prior["actor"]!=actor or prior["stage"]!=stage or prior["input_sha256"]!=hashlib.sha256(prompt.encode()).hexdigest():raise RuntimeError("request id conflict")
    if prior["status"]=="COMPLETED":return {"ok":True,"duplicate":True,"result":json.loads(prior["response_json"]),"receipt":self.receipt(dict(prior))}
   else:c.execute("INSERT INTO calls(request_id,actor,stage,requested_model,status,input_sha256,created_at) VALUES(?,?,?,?,?,?,?)",(request_id,actor,stage,model,"RUNNING",hashlib.sha256(prompt.encode()).hexdigest(),time.time()))
  call_dir=self.work_root/request_id.replace(":","_");call_dir.mkdir(parents=True,exist_ok=True);usage=call_dir/"usage.json"
  cmd=shlex.split(self.hermes, posix=os.name!="nt")+["--ignore-rules","--in",str(call_dir),"--pass-session-id","--usage-file",str(usage),"--provider",provider,"--model",model,"-z",prompt]
  p=subprocess.run(cmd,text=True,capture_output=True,timeout=int(route.get("timeout_seconds",300)))
  used=json.loads(usage.read_text(encoding="utf-8")) if usage.exists() else {}
  if p.returncode:
   with sqlite3.connect(self.db) as c:c.execute("UPDATE calls SET status='FAILED',exit_code=?,completed_at=?,usage_json=?,error=? WHERE request_id=?",(p.returncode,time.time(),json.dumps(used,sort_keys=True),p.stderr[-2000:],request_id))
   raise RuntimeError(f"Hermes exit {p.returncode}: {p.stderr[-500:]}")
  result=json_object(p.stdout);response=json.dumps(result,sort_keys=True);cost=used.get("estimated_cost_usd") if used.get("cost_status")=="known" else None
  values=(used.get("provider"),used.get("model"),used.get("session_id"),"COMPLETED",hashlib.sha256(response.encode()).hexdigest(),used.get("input_tokens"),used.get("output_tokens"),used.get("total_tokens"),cost,used.get("cost_status","unknown"),used.get("cost_source","none"),0,time.time(),response,json.dumps(used,sort_keys=True),request_id)
  with sqlite3.connect(self.db) as c:c.execute("UPDATE calls SET actual_provider=?,actual_model=?,session_id=?,status=?,output_sha256=?,input_tokens=?,output_tokens=?,total_tokens=?,cost_value=?,cost_status=?,cost_source=?,exit_code=?,completed_at=?,response_json=?,usage_json=? WHERE request_id=?",values)
  row={"request_id":request_id,"actor":actor,"stage":stage,"requested_model":model,"actual_provider":used.get("provider"),"actual_model":used.get("model"),"session_id":used.get("session_id"),"status":"COMPLETED","input_sha256":hashlib.sha256(prompt.encode()).hexdigest(),"output_sha256":hashlib.sha256(response.encode()).hexdigest(),"input_tokens":used.get("input_tokens"),"output_tokens":used.get("output_tokens"),"total_tokens":used.get("total_tokens"),"cost_value":cost,"cost_status":used.get("cost_status","unknown"),"cost_source":used.get("cost_source","none"),"exit_code":0}
  return {"ok":True,"duplicate":False,"result":result,"receipt":self.receipt(row)}
 @staticmethod
 def receipt(row:dict[str,Any])->dict[str,Any]:return {k:row.get(k) for k in ("request_id","actor","stage","requested_model","actual_provider","actual_model","session_id","status","input_sha256","output_sha256","input_tokens","output_tokens","total_tokens","cost_value","cost_status","cost_source","exit_code")}
 def handle(self,r:dict[str,Any])->dict[str,Any]:
  actor,_=self.auth(r)
  if r.get("op")=="call":return self.call(actor,r)
  if r.get("op")=="shutdown":
   if actor!="MORCH":raise PermissionError("shutdown requires MORCH")
   self.running=False;return {"ok":True}
  raise ValueError("unsupported broker operation")

def serve(a:argparse.Namespace)->int:
 path=Path(a.socket);path.parent.mkdir(parents=True,exist_ok=True);path.unlink(missing_ok=True);broker=Broker(Path(a.db),Path(a.key_dir),Path(a.work_root),Path(a.routes),a.hermes)
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
  listener.bind(str(path));listener.listen(8);listener.settimeout(1)
  while broker.running:
   try:conn,_=listener.accept()
   except TimeoutError:continue
   with conn:
    try:response=broker.handle(recv(conn))
    except Exception as exc:response={"ok":False,"error":type(exc).__name__,"detail":str(exc)}
    send(conn,response)
 path.unlink(missing_ok=True);return 0
def main()->int:
 p=argparse.ArgumentParser();p.add_argument("serve",nargs="?");p.add_argument("--socket",required=True);p.add_argument("--db",required=True);p.add_argument("--key-dir",required=True);p.add_argument("--work-root",required=True);p.add_argument("--routes",required=True);p.add_argument("--hermes",default=HERMES);return serve(p.parse_args())
if __name__=="__main__":raise SystemExit(main())

