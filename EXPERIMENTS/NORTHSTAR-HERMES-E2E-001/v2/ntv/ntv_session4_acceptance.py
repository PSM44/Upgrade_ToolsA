#!/usr/bin/env python3
"""Read-only mechanical verifier for the direct-OmniRoute Session 4 runtime."""
import argparse,json,sqlite3,sys
from pathlib import Path

V2=Path(__file__).resolve().parents[1];sys.path.insert(0,str(V2/"src"))
from operational_memory import OperationalMemory

def rows(db,query,args=()):
 with sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro",uri=True) as c:
  c.row_factory=sqlite3.Row;return [dict(x) for x in c.execute(query,args)]
def check(name,passed,detail,gate="FUNCTIONAL"):return {"name":name,"passed":bool(passed),"detail":detail,"gate":gate}

def main():
 p=argparse.ArgumentParser();p.add_argument("--runtime",required=True,type=Path);p.add_argument("--objective-id",required=True);p.add_argument("--pricing",required=True,type=Path);p.add_argument("--root-verdict",required=True,type=Path);p.add_argument("--output",required=True,type=Path);a=p.parse_args();rt=a.runtime;oid=a.objective_id
 objective=rows(rt/"control_plane.db","select state,result_json from objectives where objective_id=?",(oid,));dispatch=rows(rt/"control_plane.db","select message_id,actor,state,exit_code from dispatches where objective_id=? order by started_at",(oid,))
 transport=rows(rt/"transport.db","select message_id,message_type,state from outbox order by created_at");inbox=rows(rt/"transport.db","select message_id,state,consumer_receipt from inbox order by received_at")
 models=rows(rt/"model_calls.db","select request_id,actor,stage,requested_model,actual_provider,actual_model,total_tokens,cost_status,cost_value from calls order by created_at");delegate=rows(rt/"delegate.db","select objective_id,status,result_json from requests where objective_id=?",(oid,))
 result=json.loads(objective[0]["result_json"]) if objective and objective[0]["result_json"] else {};qa1=json.loads((rt/f"objectives/{oid}/qa/qa_round1.json").read_text());qa2=json.loads((rt/f"objectives/{oid}/qa/qa_round2.json").read_text())
 actual=sorted({x["actual_model"] for x in models if x["actual_model"]});pricing=json.loads(a.pricing.read_text());root=json.loads(a.root_verdict.read_text())
 with OperationalMemory(rt/"memory.db") as memory:memory_ok=memory.verify_chain(oid) and bool(memory.recover(oid).get("checkpoint"))
 expected=["TASK_HANDOFF","DELIVERY","QA_REJECTED","REWORK_RESPONSE","QA_PASS","ACCEPTED","RORCH_ACK"]
 price_ok=len(pricing.get("calls",[]))==len(models) and all(x["catalog_cost"]["status"]=="computed_from_public_unit_pricing" and x["catalog_cost"]["value_usd"]=="0" for x in pricing.get("calls",[]))
 checks=[
  check("morch_delegate_ingress_egress",len(delegate)==1 and delegate[0]["status"]=="COMPLETED" and result.get("human_relay_count")==0,"authenticated ingress and terminal result; HUMAN_RELAY_COUNT=0"),
  check("seven_edge_consumer_cycle",[x["message_type"] for x in transport]==expected and all(x["state"]=="ACKED" for x in transport) and len(inbox)==7 and all(x["state"]=="ACKED" and x["consumer_receipt"] for x in inbox),"exact cycle and consumer-owned receipts"),
  check("qa_reject_fix_pass",qa1["exit_code"]!=0 and qa2["exit_code"]==0 and qa1["model_verdict"]["verdict"]=="REJECTED" and qa2["model_verdict"]["verdict"]=="PASS","independent QA process plus deterministic adversarial probes"),
  check("dispatch_terminal",len(dispatch)==7 and all(x["state"]=="COMPLETED" and x["exit_code"]==0 for x in dispatch),"seven terminal unique dispatches"),
  check("memory_integrity_recovery",memory_ok,"hash chain and checkpoint recover"),
  check("two_provider_returned_models",len(actual)>=2 and all(x["actual_provider"]=="openrouter" and x["total_tokens"]>0 for x in models),f"actual_models={actual}","MULTIMODEL"),
  check("public_catalog_cost_reconciliation",price_ok,"exact returned model IDs, measured tokens, pinned public catalog unit pricing; not a billing ledger","COST"),
  check("provider_billing_receipt",all(x["cost_status"]=="known" and x["cost_value"] is not None for x in models),"completion endpoint omitted authoritative per-call billing cost; unknown remains null","BILLING"),
  check("full_ntv_root",root.get("status")=="PASS",f"root_status={root.get('status')}","ROOT"),
  check("independent_reviewer_authority",False,"verifier is a separate read-only process but no separately authorized reviewer principal signed acceptance","INDEPENDENCE"),
 ]
 payload={"schema":"upgrade-ta.northstar.session4-acceptance/v1","objective_id":oid,"checks":checks,"functional_pass":all(x["passed"] for x in checks if x["gate"] in {"FUNCTIONAL","MULTIMODEL","COST"}),"actual_models":actual,"model_calls":models,"status":"NORTHSTAR_PASS" if all(x["passed"] for x in checks) else "NORTHSTAR_FAIL"}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2,sort_keys=True),encoding="utf-8");print(json.dumps({"status":payload["status"],"passed":f"{sum(x['passed'] for x in checks)}/{len(checks)}","output":str(a.output)}));return 0 if payload["status"]=="NORTHSTAR_PASS" else 1
if __name__=="__main__":raise SystemExit(main())
