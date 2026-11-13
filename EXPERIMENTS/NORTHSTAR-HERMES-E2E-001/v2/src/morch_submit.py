#!/usr/bin/env python3
"""Authenticated local MORCH submission client (not ChatGPT activation)."""
import argparse,hashlib,json,socket
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument("--socket",required=True);p.add_argument("--key",required=True,type=Path);p.add_argument("--objective-id",required=True);p.add_argument("--objective",required=True);a=p.parse_args()
 mid=f"{a.objective_id}:task-handoff";req={"op":"send","actor":"MORCH","key":a.key.read_bytes().hex(),"recipient":"RORCH","message_type":"TASK_HANDOFF","message_id":mid,"nonce":hashlib.sha256(mid.encode()).hexdigest(),"payload":{"objective_id":a.objective_id,"objective":a.objective,"fixture_spec":"palindrome_v1","authority":"MORCH_LOCAL_CONTROL_PLANE_NOT_CHATGPT"}}
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.connect(a.socket);s.sendall(json.dumps(req,sort_keys=True,separators=(",",":")).encode()+b"\n");data=b""
  while not data.endswith(b"\n"):data+=s.recv(65536)
 r=json.loads(data.decode());print(json.dumps(r,sort_keys=True));return 0 if r.get("ok") else 3
if __name__=="__main__":raise SystemExit(main())

