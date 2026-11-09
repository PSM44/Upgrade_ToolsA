#!/usr/bin/env python3
"""Create the fail-closed Session 1 mission checkpoint from live fixture state."""
from __future__ import annotations
import argparse, json, os
from pathlib import Path
from mission_checkpoint import file_sha256, write_checkpoint

V2=Path(__file__).resolve().parents[1]
PINNED=[
 "src/persistent_control_plane.py","src/control_actor.py","src/transport_rpc.py",
 "src/secure_transport.py","src/operational_memory.py","src/model_router.py",
 "src/model_route_probe.py","src/mission_checkpoint.py","src/morch_submit.py",
 "tests/test_persistent_control_plane.py","resume_northstar.ps1",
]
def alive(pid:int)->bool:
 if os.name=="nt":
  import ctypes
  handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,pid)
  if not handle:return False
  ctypes.windll.kernel32.CloseHandle(handle);return True
 try: os.kill(pid,0);return True
 except OSError:return False
def main()->int:
 p=argparse.ArgumentParser();p.add_argument("--runtime",type=Path,required=True);p.add_argument("--source-commit",required=True);a=p.parse_args()
 runtime=a.runtime.resolve();pid_file=runtime/"launcher.pid";pid=int(pid_file.read_text().strip()) if pid_file.exists() else 0
 prior_revision=0
 if (V2/"NORTHSTAR_STATE.json").exists():
  prior_revision=int(json.loads((V2/"NORTHSTAR_STATE.json").read_text(encoding="utf-8")).get("revision",0))
 state={
  "schema_version":"upgrade-ta.northstar-state/v1","mission_id":"NORTHSTAR-DELIVERY-20261209",
  "objective_ids":["P0_PERSISTENT_CONTROL_PLANE","P2_MEMORY_RETRIEVAL","P3_MODEL_ROUTING","P4_NTV","P5_E2E"],
  "workspace_root":str(V2),"source_commit":a.source_commit,
  "source_artifacts":{rel:file_sha256(V2/rel) for rel in PINNED},
  "completed_requirements":["authenticated MORCH local submission","persistent UWS dispatcher","automatic isolated role launch","consumer-owned ACK/NACK","QA rejection and rework","MORCH result and RORCH ACK","real process restart recovery","relevant memory retrieval after restart","invalid/legitimate patch controls"],
  "remaining_tasks":["replace deterministic RORCH adapter with isolated real Hermes or compatible runtime","prove two real underlying model IDs","authoritative cost receipts","full-system NTV_ROOT including gateway","bind external MORCH authority or record MORCH_CHATGPT_ACTIVATION_BLOCKED"],
  "next_action":{"id":"resume_control_plane","command":["{python}","src/persistent_control_plane.py","run","--runtime",str(runtime),"--poll","0.1"]},
  "running_processes":[{"pid":pid,"component":"persistent_control_plane","status":"RUNNING","runtime":str(runtime)}] if pid and alive(pid) else [],
  "recovery":{"control_db":str(runtime/"control_plane.db"),"transport_db":str(runtime/"transport.db"),"memory_db":str(runtime/"memory.db"),"endpoint":str(runtime/"endpoint.json"),"behavior":"on restart, RUNNING dispatches become ABANDONED; pending/expired transport messages are reclaimed"},
  "tests":[{"name":"control_plane_integration","passed":6,"total":6,"exit_code":0},{"name":"full_v2_gate","passed":50,"total":50,"exit_code":0},{"name":"initial_absolute_path_unittest_invocation","exit_code":1,"classification":"harness invocation repaired"}],
  "external_dependencies":["authorized secret-zero broker receipt for real model calls","second distinct underlying model ID if existing route exposes one","external MORCH authority/activation interface","full-system VM or equivalent boundary for Hermes/gateway"],
  "security_restrictions":{"allowed_write_root":str(V2),"production_uta006_hold_active":True,"allow_production_service_restart":False,"original_worktree_read_only":True,"baton_protected":True,"no_new_billing":True},
  "last_independently_verified":{"baseline":"NTV_FUNCTIONAL_PASS_V4","session1_control_plane":"IMPLEMENTER_VERIFIED_PENDING_INDEPENDENT_REVIEW","full_ntv_root":"UNVERIFIED"},
  "revision":prior_revision,"updated_at":"","checkpoint_sha256":""
 }
 written=write_checkpoint(V2/"NORTHSTAR_STATE.json",state)
 print(json.dumps({"status":"CHECKPOINT_WRITTEN","revision":written["revision"],"checkpoint_sha256":written["checkpoint_sha256"],"running":bool(written["running_processes"])},sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())

