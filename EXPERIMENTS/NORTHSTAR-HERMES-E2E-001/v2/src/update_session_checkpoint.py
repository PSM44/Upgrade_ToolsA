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
 "src/model_broker.py","config/model_routes_session2.json",
 "src/morch_delegate.py","config/model_routes_session3.json",
 "tests/test_persistent_control_plane.py","tests/test_session2_model_broker.py",
 "tests/test_session2_real_runtime.py","tests/test_morch_delegate.py",
 "tests/test_session3_role_mounts.py","ntv/ntv_controlplane.py",
 "ntv/ntv_root_session3.py","ntv/ntv_session3_acceptance.py","resume_northstar.ps1",
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
 runtime=a.runtime.resolve();endpoint_file=runtime/"endpoint.json"
 endpoint=json.loads(endpoint_file.read_text(encoding="utf-8")) if endpoint_file.exists() else {}
 pid=int(endpoint.get("pid",0))
 prior_revision=0
 if (V2/"NORTHSTAR_STATE.json").exists():
  prior_revision=int(json.loads((V2/"NORTHSTAR_STATE.json").read_text(encoding="utf-8")).get("revision",0))
 state={
  "schema_version":"upgrade-ta.northstar-state/v1","mission_id":"NORTHSTAR-DELIVERY-20261209",
  "objective_ids":["P0_PERSISTENT_CONTROL_PLANE","P2_MEMORY_RETRIEVAL","P3_MODEL_ROUTING","P4_NTV","P5_E2E"],
  "workspace_root":str(V2),"source_commit":a.source_commit,
  "source_artifacts":{rel:file_sha256(V2/rel) for rel in PINNED},
  "completed_requirements":["authenticated HUMAN ingress and terminal egress through MORCH_LOCAL_DELEGATE","persistent UWS dispatcher","automatic isolated role launch","consumer-owned ACK/NACK","real Hermes RORCH build and fix","separate QA process and filesystem permissions","QA rejection and rework","MORCH result and RORCH ACK","real process restart and UWS dead-letter redrive","cross-objective memory retrieval","invalid/legitimate patch controls","secret-zero model broker ACL and idempotency","zero-tool Hermes invocation without --ignore-rules","Session 3 seven-edge E2E with HUMAN_RELAY_COUNT=0"],
  "remaining_tasks":["prove two real underlying model IDs (current receipts expose only 20261030_RORCH)","obtain authoritative cost receipts (current Hermes usage reports unknown)","contain Windows supervisor plus Hermes/OmniRoute gateway for full NTV_ROOT","obtain independently authorized reviewer acceptance"],
  "next_action":{"id":"resume_control_plane","command":["{python}","src/persistent_control_plane.py","run","--runtime",str(runtime),"--backend","hermes","--model-routes",str(V2/"config"/"model_routes_session2.json"),"--poll","0.2"]},
  "running_processes":[{"pid":pid,"component":"persistent_control_plane","status":"RUNNING","runtime":str(runtime)}] if pid and alive(pid) else [],
  "recovery":{"control_db":str(runtime/"control_plane.db"),"transport_db":str(runtime/"transport.db"),"memory_db":str(runtime/"memory.db"),"model_db":str(runtime/"model_calls.db"),"endpoint":str(runtime/"endpoint.json"),"behavior":"on restart, RUNNING dispatches become ABANDONED; pending/expired transport messages and idempotent model request IDs are reclaimed"},
  "tests":[{"name":"session3_full_regression","passed":50,"total":50,"exit_code":0,"skipped_evidence_suites_without_runtime":2},{"name":"session3_affected_security","passed":18,"total":18,"exit_code":0},{"name":"mechanical_control_plane_ntv","passed":12,"total":12,"exit_code":0},{"name":"session3_acceptance","passed":5,"total":9,"exit_code":1}],
  "external_dependencies":["second distinct underlying authorized model route","authoritative provider billing receipt","full-system VM or equivalent boundary for Windows supervisor and Hermes/OmniRoute gateway","independent reviewer authority"],
  "security_restrictions":{"allowed_write_root":str(V2),"production_uta006_hold_active":True,"allow_production_service_restart":False,"original_worktree_read_only":True,"baton_protected":True,"no_new_billing":True},
  "last_independently_verified":{"baseline":"NTV_FUNCTIONAL_PASS_V4","session3_functional":"MECHANICAL_FUNCTIONAL_PASS_5_OF_5_INDEPENDENCE_UNVERIFIED","mechanical_control_plane_ntv":"12_OF_12","full_ntv_root":"FAIL_HOST_SCOPED_HERMES_OMNIROUTE"},
  "revision":prior_revision,"updated_at":"","checkpoint_sha256":""
 }
 written=write_checkpoint(V2/"NORTHSTAR_STATE.json",state)
 print(json.dumps({"status":"CHECKPOINT_WRITTEN","revision":written["revision"],"checkpoint_sha256":written["checkpoint_sha256"],"running":bool(written["running_processes"])},sort_keys=True));return 0
if __name__=="__main__":raise SystemExit(main())

