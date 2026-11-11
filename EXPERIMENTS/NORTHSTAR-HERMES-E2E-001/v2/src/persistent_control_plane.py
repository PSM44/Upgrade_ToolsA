#!/usr/bin/env python3
"""Persistent restart-safe UWS dispatcher over the authenticated V4 transport."""
from __future__ import annotations
import argparse, hashlib, json, os, sqlite3, subprocess, time
from pathlib import Path
from typing import Any
from operational_memory import OperationalMemory

HERE=Path(__file__).resolve().parent; V2=HERE.parent; DEFAULT_RUNTIME=V2/"runtime"/"control_plane"
SCHEMA="""PRAGMA journal_mode=DELETE;
CREATE TABLE IF NOT EXISTS objectives(objective_id TEXT PRIMARY KEY,state TEXT NOT NULL,current_message_id TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL,result_json TEXT,last_error TEXT);
CREATE TABLE IF NOT EXISTS dispatches(message_id TEXT PRIMARY KEY,objective_id TEXT NOT NULL,actor TEXT NOT NULL,state TEXT NOT NULL,attempt INTEGER NOT NULL,started_at REAL NOT NULL,finished_at REAL,exit_code INTEGER,output_json TEXT);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,timestamp REAL NOT NULL,event_type TEXT NOT NULL,objective_id TEXT,detail_json TEXT NOT NULL);
"""
def wsl(path:Path)->str:
 p=path.resolve()
 if not p.drive:return p.as_posix()
 return f"/mnt/{p.drive[0].lower()}/{p.as_posix()[3:]}"

class ControlPlane:
 def __init__(self,runtime:Path,backend:str="deterministic",routes:Path|None=None):
  self.runtime=runtime.resolve(); self.runtime.mkdir(parents=True,exist_ok=True)
  self.db=self.runtime/"control_plane.db"; self.transport_db=self.runtime/"transport.db"; self.memory_db=self.runtime/"memory.db"; self.keys=self.runtime/"identity"
  token=hashlib.sha256(str(self.runtime).encode()).hexdigest()[:12]
  self.socket_dir=f"/tmp/northstar-control-{token}"; self.socket=self.socket_dir+"/transport.sock"; self.daemon=None
  self.backend=backend; self.routes=(routes or V2/"config"/"model_routes_session2.json").resolve()
  self.model_socket_dir=f"/tmp/northstar-model-{token}"; self.model_socket=self.model_socket_dir+"/model.sock"; self.model_daemon=None
  self.delegate_socket_dir=f"/tmp/northstar-delegate-{token}";self.delegate_socket=self.delegate_socket_dir+"/delegate.sock";self.delegate_daemon=None
  c=sqlite3.connect(self.db)
  try:
   c.executescript(SCHEMA); c.execute("UPDATE dispatches SET state='ABANDONED',finished_at=?,exit_code=-1 WHERE state='RUNNING'",(time.time(),))
   c.execute("INSERT INTO events(timestamp,event_type,objective_id,detail_json) VALUES(?,?,?,?)",(time.time(),"CONTROL_PLANE_STARTED",None,json.dumps({"pid":os.getpid()}))); c.commit()
  finally:c.close()
 def conn(self):
  c=sqlite3.connect(self.db,timeout=20); c.row_factory=sqlite3.Row; return c
 def restrict_identity_acls(self):
  if os.name!="nt":return
  principal=f"{os.environ.get('USERDOMAIN','')}\\{os.environ.get('USERNAME','')}"
  for path in self.keys.glob("*.key"):
   proc=subprocess.run(["icacls",str(path),"/inheritance:r","/grant:r",f"{principal}:(F)","*S-1-5-18:(F)"],text=True,capture_output=True)
   if proc.returncode:raise RuntimeError(f"identity ACL failed for {path.name}: {proc.stdout} {proc.stderr}")
 def start_transport(self):
  cmd=["wsl.exe","-d","kali-linux","--","/usr/bin/python3",wsl(HERE/"transport_rpc.py"),"serve","--db",wsl(self.transport_db),"--key-dir",wsl(self.keys),"--socket",self.socket]
  self.daemon=subprocess.Popen(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  for _ in range(200):
   ready=subprocess.run(["wsl.exe","-d","kali-linux","--","test","-S",self.socket],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
   if ready and all((self.keys/f"{a}.key").exists() for a in ("morch","rorch","qa","uws")):
    endpoint={"socket":self.socket,"pid":os.getpid(),"started_at":time.time(),"backend":self.backend}
    (self.runtime/"endpoint.json").write_text(json.dumps(endpoint,sort_keys=True),encoding="utf-8")
    return
   if self.daemon.poll() is not None:
    out,err=self.daemon.communicate(); raise RuntimeError(f"transport exited: {out} {err}")
   time.sleep(.05)
  raise RuntimeError("transport socket timeout")
 def start_model_broker(self):
  if self.backend not in {"hermes","model-broker"}: return
  cmd=["wsl.exe","-d","kali-linux","--","/usr/bin/python3",wsl(HERE/"model_broker.py"),"serve","--socket",self.model_socket,"--db",wsl(self.runtime/"model_calls.db"),"--key-dir",wsl(self.keys),"--work-root",wsl(self.runtime/"model_calls"),"--routes",wsl(self.routes)]
  self.model_daemon=subprocess.Popen(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  for _ in range(200):
   ready=subprocess.run(["wsl.exe","-d","kali-linux","--","test","-S",self.model_socket],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
   if ready:
    endpoint=json.loads((self.runtime/"endpoint.json").read_text(encoding="utf-8"));endpoint.update({"model_socket":self.model_socket,"model_broker_pid":self.model_daemon.pid,"routes":str(self.routes)})
    (self.runtime/"endpoint.json").write_text(json.dumps(endpoint,sort_keys=True),encoding="utf-8");return
   if self.model_daemon.poll() is not None:
    out,err=self.model_daemon.communicate();raise RuntimeError(f"model broker exited: {out} {err}")
   time.sleep(.05)
  raise RuntimeError("model broker socket timeout")
 def stop_model_broker(self):
  if not self.model_daemon or self.model_daemon.poll() is not None:return
  request={"op":"shutdown","actor":"MORCH","key":(self.keys/"morch.key").read_bytes().hex()}
  code="import json,socket,sys;s=socket.socket(socket.AF_UNIX);s.connect(sys.argv[1]);s.sendall((json.dumps(json.loads(sys.argv[2]))+'\\n').encode());print(s.recv(4096).decode())"
  subprocess.run(["wsl.exe","-d","kali-linux","--","/usr/bin/python3","-c",code,self.model_socket,json.dumps(request)],capture_output=True,text=True,timeout=15)
  self.model_daemon.wait(timeout=15)
 def start_delegate(self):
  cmd=["wsl.exe","-d","kali-linux","--","/usr/bin/python3",wsl(HERE/"morch_delegate.py"),"serve","--socket",self.delegate_socket,"--db",wsl(self.runtime/"delegate.db"),"--transport-socket",self.socket,"--control-db",wsl(self.db),"--key-dir",wsl(self.keys)]
  self.delegate_daemon=subprocess.Popen(cmd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  for _ in range(200):
   ready=subprocess.run(["wsl.exe","-d","kali-linux","--","test","-S",self.delegate_socket],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
   if ready:
    self.restrict_identity_acls()
    endpoint=json.loads((self.runtime/"endpoint.json").read_text(encoding="utf-8"));endpoint.update({"delegate_socket":self.delegate_socket,"delegate_pid":self.delegate_daemon.pid,"authority":"MORCH_LOCAL_DELEGATE"})
    (self.runtime/"endpoint.json").write_text(json.dumps(endpoint,sort_keys=True),encoding="utf-8");return
   if self.delegate_daemon.poll() is not None:
    out,err=self.delegate_daemon.communicate();raise RuntimeError(f"delegate exited: {out} {err}")
   time.sleep(.05)
  raise RuntimeError("delegate socket timeout")
 def stop_delegate(self):
  if not self.delegate_daemon or self.delegate_daemon.poll() is not None:return
  subprocess.run(["wsl.exe","-d","kali-linux","--","/usr/bin/python3",wsl(HERE/"morch_delegate.py"),"client","--socket",self.delegate_socket,"--key",wsl(self.keys/"human.key"),"--operation","shutdown"],capture_output=True,text=True,timeout=15)
  self.delegate_daemon.wait(timeout=15)
 def stop_transport(self):
  if self.daemon and self.daemon.poll() is None:
   subprocess.run(["wsl.exe","-d","kali-linux","--","/usr/bin/python3",wsl(HERE/"transport_rpc.py"),"client","--socket",self.socket,"--key",wsl(self.keys/"uws.key"),"--actor","UWS","shutdown"],capture_output=True,text=True,timeout=15)
   self.daemon.wait(timeout=15)
 def pending(self):
  if not self.transport_db.exists(): return []
  c=sqlite3.connect(f"file:{self.transport_db.as_posix()}?mode=ro",uri=True); c.row_factory=sqlite3.Row
  try: return c.execute("SELECT message_id,recipient_actor,payload_json,message_type FROM outbox WHERE state='PENDING' OR (state='CLAIMED' AND lease_expires_at<=?) ORDER BY created_at,message_id",(time.time(),)).fetchall()
  finally: c.close()
 def objective(self,msg):
  payload=json.loads(msg["payload_json"]); oid=str(payload["objective_id"]); now=time.time()
  with self.conn() as c:
   existed=c.execute("SELECT 1 FROM objectives WHERE objective_id=?",(oid,)).fetchone() is not None
   c.execute("INSERT OR IGNORE INTO objectives VALUES(?,?,?,?,?,?,?)",(oid,"ACTIVE",msg["message_id"],now,now,None,None))
  if not existed:
   with OperationalMemory(self.memory_db) as memory:
    memory.append(task_id=oid,kind="OBJECTIVE",actor="MORCH_LOCAL_DELEGATE",payload=payload,record_id=f"objective:{oid}")
  return oid
 def worker_cmd(self,actor:str,mid:str,oid:str):
  app=self.runtime/"objectives"/oid/"app"; dev=self.runtime/"objectives"/oid/"dev"; qa=self.runtime/"objectives"/oid/"qa"; app.mkdir(parents=True,exist_ok=True); dev.mkdir(parents=True,exist_ok=True); qa.mkdir(parents=True,exist_ok=True)
  cmd=["wsl.exe","-d","kali-linux","--","bwrap","--unshare-all","--die-with-parent","--new-session","--clearenv","--ro-bind","/usr","/usr","--ro-bind","/bin","/bin","--ro-bind","/lib","/lib","--ro-bind","/lib64","/lib64","--ro-bind","/etc","/etc","--ro-bind",wsl(HERE),"/code","--ro-bind",wsl(self.keys/f"{actor.lower()}.key"),"/identity/key.bin","--bind",self.socket_dir,"/ipc","--proc","/proc","--dev","/dev","--tmpfs","/tmp","--setenv","HOME","/tmp/home","--setenv","PYTHONDONTWRITEBYTECODE","1"]
  if actor=="RORCH": cmd += ["--bind",wsl(app),"/role/app","--bind",wsl(dev),"/role/dev"]
  elif actor=="QA": cmd += ["--ro-bind",wsl(app),"/role/app","--ro-bind",wsl(dev),"/role/dev","--bind",wsl(qa),"/role/qa"]
  else: cmd += ["--ro-bind",wsl(app),"/role/app","--ro-bind",wsl(dev),"/role/dev","--ro-bind",wsl(qa),"/role/qa"]
  if self.backend in {"hermes","model-broker"}:cmd += ["--bind",self.model_socket_dir,"/model"]
  result=cmd+["--chdir","/code","/usr/bin/python3","/code/control_actor.py","--actor",actor,"--key","/identity/key.bin","--socket","/ipc/transport.sock","--message-id",mid,"--backend",self.backend]
  if self.backend in {"hermes","model-broker"}:result += ["--model-socket","/model/model.sock"]
  return result
 def reconcile(self):
  p=subprocess.run(["wsl.exe","-d","kali-linux","--","/usr/bin/python3",wsl(HERE/"transport_rpc.py"),"client","--socket",self.socket,"--key",wsl(self.keys/"uws.key"),"--actor","UWS","reconcile"],text=True,capture_output=True,timeout=20)
  if p.returncode: raise RuntimeError(p.stdout+p.stderr)
 def dispatch_once(self):
  rows=self.pending()
  if not rows:return False
  msg=rows[0]; oid=self.objective(msg)
  with self.conn() as c:
   if c.execute("SELECT state FROM objectives WHERE objective_id=?",(oid,)).fetchone()[0]=="FAILED":return False
  actor=str(msg["recipient_actor"]); mid=str(msg["message_id"]); now=time.time()
  with self.conn() as c:
   old=c.execute("SELECT attempt FROM dispatches WHERE message_id=?",(mid,)).fetchone(); attempt=(old["attempt"] if old else 0)+1
   c.execute("INSERT INTO dispatches VALUES(?,?,?,?,?,?,NULL,NULL,NULL) ON CONFLICT(message_id) DO UPDATE SET state='RUNNING',attempt=?,started_at=?,finished_at=NULL,exit_code=NULL,output_json=NULL",(mid,oid,actor,"RUNNING",attempt,now,attempt,now))
   c.execute("INSERT INTO events(timestamp,event_type,objective_id,detail_json) VALUES(?,?,?,?)",(now,"WORKER_STARTED",oid,json.dumps({"actor":actor,"message_id":mid,"attempt":attempt},sort_keys=True)))
  p=subprocess.run(self.worker_cmd(actor,mid,oid),text=True,capture_output=True,timeout=360); state="COMPLETED" if p.returncode==0 else "FAILED"
  with self.conn() as c:
   c.execute("UPDATE dispatches SET state=?,finished_at=?,exit_code=?,output_json=? WHERE message_id=?",(state,time.time(),p.returncode,json.dumps({"stdout":p.stdout[-4000:],"stderr":p.stderr[-4000:]}),mid))
   c.execute("UPDATE objectives SET current_message_id=?,updated_at=?,last_error=? WHERE objective_id=?",(mid,time.time(),None if p.returncode==0 else p.stderr[-1000:],oid))
   c.execute("INSERT INTO events(timestamp,event_type,objective_id,detail_json) VALUES(?,?,?,?)",(time.time(),"WORKER_"+state,oid,json.dumps({"actor":actor,"message_id":mid,"exit_code":p.returncode},sort_keys=True)))
  if p.returncode:
   payload=json.loads(msg["payload_json"])
   if payload.get("fixture_spec")=="retry_schedule_v1":
    result={"objective_id":oid,"status":"FAILED","human_relay_count":0,"reason":"P2_WORKER_FAILED","completed_at":time.time()}
    with self.conn() as c:c.execute("UPDATE objectives SET state='FAILED',result_json=?,updated_at=? WHERE objective_id=?",(json.dumps(result,sort_keys=True),time.time(),oid))
    return True
   raise RuntimeError(f"worker {actor}/{mid}: {p.stderr[-1000:]}")
  worker_result={}
  try:worker_result=json.loads(p.stdout.strip().splitlines()[-1])
  except (ValueError,IndexError):pass
  if worker_result.get("terminal_failure"):
   result={"objective_id":oid,"status":"FAILED","human_relay_count":0,"reason":worker_result.get("reason","P2_REWORK_EXHAUSTED"),"completed_at":time.time()}
   with self.conn() as c:c.execute("UPDATE objectives SET state='FAILED',result_json=?,updated_at=? WHERE objective_id=?",(json.dumps(result,sort_keys=True),time.time(),oid))
   return True
  with OperationalMemory(self.memory_db) as memory:
   if not any(r.kind=="CHECKPOINT" and r.payload.get("event_id")==mid for r in memory.records(oid)):
    memory.checkpoint(task_id=oid,state="MESSAGE_CONSUMED",event_id=mid,context={"actor":actor,"message_type":msg["message_type"]})
  self.reconcile()
  if msg["message_type"]=="RORCH_ACK":
   result={"objective_id":oid,"status":"ACCEPTED","human_relay_count":0,"completed_at":time.time()}
   payload=json.loads(msg["payload_json"])
   if payload.get("fixture_spec")=="retry_schedule_v1":
    if not worker_result.get("terminal_metadata"):raise RuntimeError("P2 terminal metadata missing")
    result["evidence"]={k:v for k,v in payload.items() if k not in {"objective_id","decision"}}
   with self.conn() as c:
    c.execute("UPDATE objectives SET state='COMPLETED',result_json=?,updated_at=? WHERE objective_id=?",(json.dumps(result,sort_keys=True),time.time(),oid))
    c.execute("INSERT INTO events(timestamp,event_type,objective_id,detail_json) VALUES(?,?,?,?)",(time.time(),"OBJECTIVE_COMPLETED",oid,json.dumps(result,sort_keys=True)))
   with OperationalMemory(self.memory_db) as memory:
    if not any(r.record_id==f"result:{oid}" for r in memory.records(oid)):
     memory.append(task_id=oid,kind="RESULT",actor="UWS",payload=result,record_id=f"result:{oid}")
  return True
 def run(self,poll:float,max_idle:float|None):
  stop=self.runtime/"STOP"
  if stop.exists(): stop.unlink()
  idle=time.time()
  try:
   self.start_transport(); self.start_model_broker(); self.start_delegate()
   while not stop.exists():
    try: worked=self.dispatch_once()
    except Exception as exc:
     with self.conn() as c:c.execute("INSERT INTO events(timestamp,event_type,objective_id,detail_json) VALUES(?,?,?,?)",(time.time(),"DISPATCH_ERROR",None,json.dumps({"error":f"{type(exc).__name__}: {exc}"})))
     worked=False
    if worked: idle=time.time()
    elif max_idle is not None and time.time()-idle>=max_idle: break
    time.sleep(poll)
  finally:
   try:self.stop_delegate()
   finally:
    try:self.stop_model_broker()
    finally:self.stop_transport()
  return 0

def status(runtime:Path)->dict[str,Any]:
 db=runtime/"control_plane.db"
 if not db.exists():return {"status":"NOT_INITIALIZED"}
 with sqlite3.connect(db) as c:
  c.row_factory=sqlite3.Row
  return {"objectives":[dict(r) for r in c.execute("SELECT * FROM objectives ORDER BY created_at")],"dispatches":[dict(r) for r in c.execute("SELECT * FROM dispatches ORDER BY started_at")],"event_count":c.execute("SELECT count(*) FROM events").fetchone()[0]}
def main():
 p=argparse.ArgumentParser();p.add_argument("command",choices=("run","status"));p.add_argument("--runtime",type=Path,default=DEFAULT_RUNTIME);p.add_argument("--poll",type=float,default=.2);p.add_argument("--max-idle",type=float);p.add_argument("--backend",choices=("deterministic","model-broker","hermes"),default="deterministic");p.add_argument("--model-routes",type=Path);a=p.parse_args()
 if a.command=="status":print(json.dumps(status(a.runtime),indent=2,sort_keys=True));return 0
 return ControlPlane(a.runtime,a.backend,a.model_routes).run(a.poll,a.max_idle)
if __name__=="__main__":raise SystemExit(main())
