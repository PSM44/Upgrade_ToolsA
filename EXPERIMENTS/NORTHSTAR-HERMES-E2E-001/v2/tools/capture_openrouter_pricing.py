#!/usr/bin/env python3
"""Capture exact-model public pricing and reconcile it with measured call usage."""
import argparse,hashlib,json,sqlite3,urllib.request
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path

URL="https://openrouter.ai/api/v1/models"

def main():
 p=argparse.ArgumentParser();p.add_argument("--model-db",required=True,type=Path);p.add_argument("--output",required=True,type=Path);a=p.parse_args()
 raw=urllib.request.urlopen(URL,timeout=30).read();catalog=json.loads(raw);models={x["id"]:x for x in catalog.get("data",[])}
 with sqlite3.connect(f"file:{a.model_db.as_posix()}?mode=ro",uri=True) as db:
  db.row_factory=sqlite3.Row;calls=[dict(x) for x in db.execute("select request_id,actual_model,input_tokens,output_tokens,total_tokens,cost_status,cost_value from calls where status='COMPLETED' order by created_at")]
 selected={};computed=[]
 for call in calls:
  mid=call["actual_model"];entry=models.get(mid);pricing=(entry or {}).get("pricing") or {}
  selected[mid]={"found":entry is not None,"pricing":pricing,"canonical_slug":(entry or {}).get("canonical_slug")}
  try:
   prompt=Decimal(str(pricing["prompt"]));completion=Decimal(str(pricing["completion"]));value=prompt*int(call["input_tokens"] or 0)+completion*int(call["output_tokens"] or 0)
   estimate={"status":"computed_from_public_unit_pricing","value_usd":str(value),"formula":"prompt_tokens*prompt_unit_price + completion_tokens*completion_unit_price"}
  except Exception:estimate={"status":"unavailable","value_usd":None}
  computed.append({**call,"catalog_cost":estimate})
 payload={"schema":"upgrade-ta.openrouter-pricing-reconciliation/v1","captured_at":datetime.now(timezone.utc).isoformat(),"source_url":URL,"catalog_sha256":hashlib.sha256(raw).hexdigest(),"selected_models":selected,"calls":computed,"limitation":"Public catalog pricing is authoritative unit pricing, not a provider billing ledger. The completion response did not return cost."}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(payload,indent=2,sort_keys=True),encoding="utf-8");print(json.dumps({"output":str(a.output),"catalog_sha256":payload["catalog_sha256"],"models":len(selected),"calls":len(calls)}))
if __name__=="__main__":main()
