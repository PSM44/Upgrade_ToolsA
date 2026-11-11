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
BROKER_RULES="""# NORTHSTAR model-broker execution policy

This is a tool-free transformation request. Do not invoke tools, shells,
plugins, MCP servers, network clients, filesystem APIs, Skills or delegation.
Read only the prompt and return exactly the requested JSON object. The broker
will reject non-JSON output and independently validate every resulting file.
"""
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
  route=self.routes["roles"][actor];model=str(route["model"]);provider=str(route.get("provider","custom"));runtime=str(route.get("runtime","hermes"))
  limits=self.routes.get("limits",{})
  if len(prompt)>int(limits.get("max_prompt_chars",12000)):raise ValueError("prompt budget exceeded")
  max_calls=int(limits.get("max_calls",8))
  with sqlite3.connect(self.db) as c:
   c.row_factory=sqlite3.Row;prior=c.execute("SELECT * FROM calls WHERE request_id=?",(request_id,)).fetchone()
   if prior:
    if prior["actor"]!=actor or prior["stage"]!=stage or prior["input_sha256"]!=hashlib.sha256(prompt.encode()).hexdigest():raise RuntimeError("request id conflict")
    if prior["status"]=="COMPLETED":return {"ok":True,"duplicate":True,"result":json.loads(prior["response_json"]),"receipt":self.receipt(dict(prior))}
   else:
    if c.execute("SELECT count(*) FROM calls").fetchone()[0]>=max_calls:raise RuntimeError("model call budget exhausted")
    c.execute("INSERT INTO calls(request_id,actor,stage,requested_model,status,input_sha256,created_at) VALUES(?,?,?,?,?,?,?)",(request_id,actor,stage,model,"RUNNING",hashlib.sha256(prompt.encode()).hexdigest(),time.time()))
  call_dir=self.work_root/request_id.replace(":","_");call_dir.mkdir(parents=True,exist_ok=True);usage=call_dir/"usage.json"
  (call_dir/"AGENTS.md").write_text(BROKER_RULES,encoding="utf-8")
  if runtime == "omniroute":
   return self.call_omniroute(actor,stage,request_id,prompt,route,model,provider)
  if runtime == "opencode":
   return self.call_opencode(actor,stage,request_id,prompt,route,model)
  if runtime != "hermes":raise ValueError(f"unsupported model runtime: {runtime}")
  # Hermes treats an explicitly empty toolset list as the supported zero-tool
  # state.  The literal name ``none`` is an unknown toolset and the gateway
  # rejects it as invalid_params.
  cmd=shlex.split(self.hermes, posix=os.name!="nt")+["--in",str(call_dir),"--toolsets","","--pass-session-id","--usage-file",str(usage),"--provider",provider,"--model",model,"-z",prompt]
  safe_env=os.environ.copy();safe_env.update({"HERMES_IGNORE_RULES":"0","HERMES_YOLO_MODE":"0","HERMES_ACCEPT_HOOKS":"0"})
  p=subprocess.run(cmd,text=True,capture_output=True,timeout=int(route.get("timeout_seconds",300)),env=safe_env)
  used=json.loads(usage.read_text(encoding="utf-8")) if usage.exists() else {}
  if p.returncode:
   with sqlite3.connect(self.db) as c:c.execute("UPDATE calls SET status='FAILED',exit_code=?,completed_at=?,usage_json=?,error=? WHERE request_id=?",(p.returncode,time.time(),json.dumps(used,sort_keys=True),p.stderr[-2000:],request_id))
   raise RuntimeError(f"Hermes exit {p.returncode}: {p.stderr[-500:]}")
  result=json_object(p.stdout);response=json.dumps(result,sort_keys=True);cost=used.get("estimated_cost_usd") if used.get("cost_status")=="known" else None
  values=(used.get("provider"),used.get("model"),used.get("session_id"),"COMPLETED",hashlib.sha256(response.encode()).hexdigest(),used.get("input_tokens"),used.get("output_tokens"),used.get("total_tokens"),cost,used.get("cost_status","unknown"),used.get("cost_source","none"),0,time.time(),response,json.dumps(used,sort_keys=True),request_id)
  with sqlite3.connect(self.db) as c:c.execute("UPDATE calls SET actual_provider=?,actual_model=?,session_id=?,status=?,output_sha256=?,input_tokens=?,output_tokens=?,total_tokens=?,cost_value=?,cost_status=?,cost_source=?,exit_code=?,completed_at=?,response_json=?,usage_json=?,error=NULL WHERE request_id=?",values)
  row={"request_id":request_id,"actor":actor,"stage":stage,"requested_model":model,"actual_provider":used.get("provider"),"actual_model":used.get("model"),"session_id":used.get("session_id"),"status":"COMPLETED","input_sha256":hashlib.sha256(prompt.encode()).hexdigest(),"output_sha256":hashlib.sha256(response.encode()).hexdigest(),"input_tokens":used.get("input_tokens"),"output_tokens":used.get("output_tokens"),"total_tokens":used.get("total_tokens"),"cost_value":cost,"cost_status":used.get("cost_status","unknown"),"cost_source":used.get("cost_source","none"),"exit_code":0}
  return {"ok":True,"duplicate":False,"result":result,"receipt":self.receipt(row)}
 def call_opencode(self,actor:str,stage:str,request_id:str,prompt:str,route:dict[str,Any],model:str)->dict[str,Any]:
  call_dir=self.work_root/request_id.replace(":","_")
  config={"$schema":"https://opencode.ai/config.json","permissions":[{"action":"*","resource":"*","effect":"deny"},{"action":"external_directory","resource":"*","effect":"deny"}]}
  config_path=call_dir/"opencode.json";config_path.write_text(json.dumps(config,sort_keys=True),encoding="utf-8");config_path.chmod(0o600)
  binary=str(route.get("binary","/home/psm/.opencode/bin/opencode"))
  command=[binary,"run","--standalone","--archived","--format","json","--model",model,prompt]
  deny={"OPENAI_API_KEY","ANTHROPIC_API_KEY","OPENROUTER_API_KEY","SSH_AUTH_SOCK","GIT_ASKPASS"}
  env={k:v for k,v in os.environ.items() if k not in deny}
  try:
   p=subprocess.run(command,cwd=call_dir,env=env,stdin=subprocess.DEVNULL,text=True,capture_output=True,timeout=int(route.get("timeout_seconds",300)))
  except subprocess.TimeoutExpired as exc:
   detail=f"OpenCode timeout after {exc.timeout} seconds"
   with sqlite3.connect(self.db) as c:c.execute("UPDATE calls SET status='FAILED',exit_code=?,completed_at=?,usage_json=?,error=? WHERE request_id=?",(-9,time.time(),json.dumps({"timeout_seconds":exc.timeout},sort_keys=True),detail,request_id))
   raise RuntimeError(detail) from exc

  (call_dir/"opencode.jsonl").write_text(p.stdout+p.stderr,encoding="utf-8")
  events=[]
  for line in p.stdout.splitlines():
   try:event=json.loads(line)
   except json.JSONDecodeError:continue
   if isinstance(event,dict):events.append(event)
  texts=[str(e.get("part",{}).get("text","")) for e in events if e.get("type")=="text"]
  finishes=[e.get("part",{}) for e in events if e.get("type")=="step_finish"]
  session=next((str(e.get("sessionID")) for e in events if e.get("sessionID")),None)
  tokens={"input":0,"output":0,"reasoning":0}
  for part in finishes:
   usage=part.get("tokens") or {}
   for key in tokens:tokens[key]+=int(usage.get(key) or 0)
  cost=sum(float(part.get("cost") or 0) for part in finishes)
  if p.returncode or not texts:
   detail=(p.stderr or p.stdout)[-2000:]
   with sqlite3.connect(self.db) as c:c.execute("UPDATE calls SET status='FAILED',exit_code=?,completed_at=?,usage_json=?,error=? WHERE request_id=?",(p.returncode,time.time(),json.dumps({"tokens":tokens,"cost":cost},sort_keys=True),detail,request_id))
   raise RuntimeError(f"OpenCode exit {p.returncode}: {detail[-500:]}")
  result=json_object(texts[-1]);response=json.dumps(result,sort_keys=True);total=tokens["input"]+tokens["output"]
  used={"provider":"opencode-go","model":model,"session_id":session,"tokens":tokens,"cost":cost,"allowance":"not_exposed_by_cli"}
  values=("opencode-go",model,session,"COMPLETED",hashlib.sha256(response.encode()).hexdigest(),tokens["input"],tokens["output"],total,cost,"provider_reported","opencode_step_finish",0,time.time(),response,json.dumps(used,sort_keys=True),request_id)
  with sqlite3.connect(self.db) as c:c.execute("UPDATE calls SET actual_provider=?,actual_model=?,session_id=?,status=?,output_sha256=?,input_tokens=?,output_tokens=?,total_tokens=?,cost_value=?,cost_status=?,cost_source=?,exit_code=?,completed_at=?,response_json=?,usage_json=?,error=NULL WHERE request_id=?",values)
  row={"request_id":request_id,"actor":actor,"stage":stage,"requested_model":model,"actual_provider":"opencode-go","actual_model":model,"session_id":session,"status":"COMPLETED","input_sha256":hashlib.sha256(prompt.encode()).hexdigest(),"output_sha256":hashlib.sha256(response.encode()).hexdigest(),"input_tokens":tokens["input"],"output_tokens":tokens["output"],"total_tokens":total,"cost_value":cost,"cost_status":"provider_reported","cost_source":"opencode_step_finish","exit_code":0}
  return {"ok":True,"duplicate":False,"result":result,"receipt":self.receipt(row)}
 def call_omniroute(self,actor:str,stage:str,request_id:str,prompt:str,route:dict[str,Any],model:str,provider:str)->dict[str,Any]:
  client=Path(str(route.get("client",Path(__file__).with_name("omniroute_direct.mjs"))))
  request={"operation":"chat","request_id":request_id,"model":model,"messages":[{"role":"system","content":BROKER_RULES},{"role":"user","content":prompt}],"max_tokens":int(route.get("max_tokens",1024)),"temperature":float(route.get("temperature",0)),"timeout_ms":int(route.get("timeout_seconds",300))*1000}
  p=subprocess.run([str(route.get("node","node")),str(client)],input=json.dumps(request,separators=(",",":")),text=True,capture_output=True,timeout=int(route.get("timeout_seconds",300))+5)
  if p.returncode:
   detail=(p.stdout or p.stderr)[-2000:]
   with sqlite3.connect(self.db) as c:c.execute("UPDATE calls SET status='FAILED',exit_code=?,completed_at=?,error=? WHERE request_id=?",(p.returncode,time.time(),detail,request_id))
   raise RuntimeError(f"OmniRoute exit {p.returncode}: {detail[-500:]}")
  envelope=json.loads(p.stdout);data=envelope.get("data") or {};choices=data.get("choices") or []
  message=(choices[0].get("message") or {}) if choices else {};content=message.get("content")
  if not isinstance(content,str) or not content.strip():
   with sqlite3.connect(self.db) as c:c.execute("UPDATE calls SET status='FAILED',exit_code=?,completed_at=?,error=? WHERE request_id=?",(3,time.time(),"OmniRoute response had no final content",request_id))
   raise RuntimeError("OmniRoute response had no final content")
  result=json_object(content);response=json.dumps(result,sort_keys=True);used=data.get("usage") or {}
  cost=used.get("cost");cost_known=isinstance(cost,(int,float)) and not isinstance(cost,bool)
  actual_model=data.get("model");actual_provider="openrouter" if model.startswith("openrouter/") else provider
  usage_receipt={"response_id":data.get("id"),"provider":actual_provider,"requested_model":model,"actual_model":actual_model,"finish_reason":choices[0].get("finish_reason"),"usage":used}
  values=(actual_provider,actual_model,data.get("id"),"COMPLETED",hashlib.sha256(response.encode()).hexdigest(),used.get("prompt_tokens"),used.get("completion_tokens"),used.get("total_tokens"),cost if cost_known else None,"known" if cost_known else "unknown","provider_response_usage" if cost_known else "not_returned",0,time.time(),response,json.dumps(usage_receipt,sort_keys=True),request_id)
  with sqlite3.connect(self.db) as c:c.execute("UPDATE calls SET actual_provider=?,actual_model=?,session_id=?,status=?,output_sha256=?,input_tokens=?,output_tokens=?,total_tokens=?,cost_value=?,cost_status=?,cost_source=?,exit_code=?,completed_at=?,response_json=?,usage_json=?,error=NULL WHERE request_id=?",values)
  row={"request_id":request_id,"actor":actor,"stage":stage,"requested_model":model,"actual_provider":actual_provider,"actual_model":actual_model,"session_id":data.get("id"),"status":"COMPLETED","input_sha256":hashlib.sha256(prompt.encode()).hexdigest(),"output_sha256":hashlib.sha256(response.encode()).hexdigest(),"input_tokens":used.get("prompt_tokens"),"output_tokens":used.get("completion_tokens"),"total_tokens":used.get("total_tokens"),"cost_value":cost if cost_known else None,"cost_status":"known" if cost_known else "unknown","cost_source":"provider_response_usage" if cost_known else "not_returned","exit_code":0}
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
 path=Path(a.socket);path.parent.mkdir(parents=True,exist_ok=True);path.parent.chmod(0o700);path.unlink(missing_ok=True);broker=Broker(Path(a.db),Path(a.key_dir),Path(a.work_root),Path(a.routes),a.hermes)
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
  listener.bind(str(path));path.chmod(0o600);listener.listen(8);listener.settimeout(1)
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
