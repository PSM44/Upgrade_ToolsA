#!/usr/bin/env python3
"""Read-only mechanical acceptance verifier for the Session 3 runtime."""
from __future__ import annotations
import argparse, json, sqlite3, sys
from pathlib import Path

V2=Path(__file__).resolve().parents[1];sys.path.insert(0,str(V2/"src"))
from operational_memory import OperationalMemory

def rows(db:Path,query:str):
    with sqlite3.connect(f"file:{db.as_posix()}?mode=ro",uri=True) as c:
        c.row_factory=sqlite3.Row;return [dict(x) for x in c.execute(query)]
def check(name:str,passed:bool,detail:str,gate:str="FUNCTIONAL"):return {"name":name,"passed":passed,"detail":detail,"gate":gate}

def main()->int:
    p=argparse.ArgumentParser();p.add_argument("--runtime",required=True,type=Path);p.add_argument("--root-verdict",required=True,type=Path);p.add_argument("--output",required=True,type=Path);a=p.parse_args();rt=a.runtime
    objective=rows(rt/"control_plane.db","select objective_id,state,result_json from objectives where objective_id='SESSION3-FINAL-001'")
    dispatch=rows(rt/"control_plane.db","select message_id,actor,state,exit_code from dispatches where objective_id='SESSION3-FINAL-001' order by started_at")
    transport=rows(rt/"transport.db","select message_id,message_type,state from outbox order by created_at")
    models=rows(rt/"model_calls.db","select request_id,status,actual_provider,actual_model,total_tokens,cost_status,cost_value from calls order by created_at")
    delegate=rows(rt/"delegate.db","select objective_id,status,result_json from requests")
    result=json.loads(objective[0]["result_json"]) if objective and objective[0]["result_json"] else {}
    expected=["TASK_HANDOFF","DELIVERY","QA_REJECTED","REWORK_RESPONSE","QA_PASS","ACCEPTED","RORCH_ACK"]
    qa1=json.loads((rt/"objectives/SESSION3-FINAL-001/qa/qa_round1.json").read_text());qa2=json.loads((rt/"objectives/SESSION3-FINAL-001/qa/qa_round2.json").read_text())
    actual_models=sorted({x["actual_model"] for x in models if x["status"]=="COMPLETED" and x["actual_model"]})
    root=json.loads(a.root_verdict.read_text())
    with OperationalMemory(rt/"memory.db") as memory: memory_ok=memory.verify_chain("SESSION3-FINAL-001") and bool(memory.recover("SESSION3-FINAL-001").get("checkpoint"))
    checks=[
      check("morch_delegate_ingress_egress",len(delegate)==1 and delegate[0]["status"]=="COMPLETED" and result.get("human_relay_count")==0,"one authenticated HUMAN objective and terminal result"),
      check("seven_edge_cycle",[x["message_type"] for x in transport]==expected and all(x["state"]=="ACKED" for x in transport),"exact ordered autonomous cycle, all consumer ACKed"),
      check("independent_qa_reject_then_pass",qa1["exit_code"]!=0 and qa2["exit_code"]==0,"separate QA process and deterministic adversarial evidence"),
      check("dispatch_terminal",len(dispatch)==7 and all(x["state"]=="COMPLETED" and x["exit_code"]==0 for x in dispatch),"seven unique terminal dispatches"),
      check("memory_integrity_recovery",memory_ok,"hash chain and latest checkpoint recover"),
      check("two_actual_models",len(actual_models)>=2,f"actual_models={actual_models}","MULTIMODEL"),
      check("authoritative_cost",all(x["cost_status"]=="known" and x["cost_value"] is not None for x in models),"UNKNOWN is not zero","COST"),
      check("full_ntv_root",root.get("status")=="PASS",f"root_status={root.get('status')}","ROOT"),
      check("independent_reviewer_identity",False,"mechanical verifier is a separate process but no independent reviewer authority was available in this session","INDEPENDENCE"),
    ]
    payload={"schema":"upgrade-ta.northstar.session3-acceptance/v1","objective_id":"SESSION3-FINAL-001","checks":checks,"functional_pass":all(x["passed"] for x in checks if x["gate"]=="FUNCTIONAL"),"actual_models":actual_models,"model_calls":models,"status":"NORTHSTAR_PASS" if all(x["passed"] for x in checks) else "NORTHSTAR_FAIL"}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2,sort_keys=True),encoding="utf-8");print(json.dumps({"status":payload["status"],"passed":f"{sum(x['passed'] for x in checks)}/{len(checks)}","output":str(a.output)}));return 0 if payload["status"]=="NORTHSTAR_PASS" else 1
if __name__=="__main__":raise SystemExit(main())

