#!/usr/bin/env python3
"""Read-only mini-core acceptance gate for the Slice 1 brain contract."""
import argparse,hashlib,json,platform,sqlite3,sys,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pydantic
from brain import Brain,BrainConfig
from brain.errors import BrainError

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def percentile(values,p): return sorted(values)[min(len(values)-1,int((len(values)-1)*p))]
def main():
 p=argparse.ArgumentParser();p.add_argument("--journal-db",required=True);p.add_argument("--retrieval-db",required=True);p.add_argument("--manifest",default="sqlite-spike/dataset/queries.json");p.add_argument("--baseline",default="sqlite-spike/results/vector-baseline.json");p.add_argument("--contract-baseline",default="sqlite-spike/results/vector-contract-baseline-v1.json");p.add_argument("--parity-audit",default="sqlite-spike/results/brain-slice1-parity-audit.json");p.add_argument("--output",default="sqlite-spike/results/brain-slice1-live.json");a=p.parse_args()
 report={"timestamp":datetime.now(timezone.utc).isoformat(),"python_version":platform.python_version(),"pydantic_version":pydantic.__version__,"journal_db_path":a.journal_db,"retrieval_db_path":a.retrieval_db,"failure_reasons":[]}
 if not Path(a.journal_db).is_file() or not Path(a.retrieval_db).is_file(): report.update(status="NOT EVALUATED",failure_reasons=["required DB path missing"]);Path(a.output).write_text(json.dumps(report,indent=2)+"\n");return 2
 before_journal,before_retrieval=sha(a.journal_db),sha(a.retrieval_db)
 config=BrainConfig(journal_db_path=Path(a.journal_db),retrieval_db_path=Path(a.retrieval_db));brain=Brain(config);manifest=json.loads(Path(a.manifest).read_text());baseline={x["query_id"]:x for x in json.loads(Path(a.baseline).read_text())["queries"]};contract={x["query_id"]:x for x in json.loads(Path(a.contract_baseline).read_text())["results"]}
 lat=[];order_ok=[];contract_ok=[];deterministic=[];workspace=sensitive=forbidden=0;provenance=True;mismatches=[]
 try:
  health=brain.health();meta=brain._meta()
  for q in manifest:
   args={"query":q["query"],"workspaces":q["scope"],"types":q["filters"]["types"],"mode":q["filters"]["mode"],"sensitive_allowed":q["filters"]["sensitive_allowed"],"limit":3}
   start=time.perf_counter();first=brain.search(**args);lat.append((time.perf_counter()-start)*1000);second=brain.search(**args)
   ids=[x.record_id for x in first.results];expected=[x["record_id"] for x in baseline[q["id"]]["returned"][:3]];order_ok.append(ids==expected)
   contract_ids=[x["record_id"] for x in contract[q["id"]]["results"][:3]];contract_ok.append(ids==contract_ids)
   if ids!=expected: mismatches.append({"query_id":q["id"],"actual":ids,"expected":expected})
   deterministic.append([x.model_dump() for x in first.results]==[x.model_dump() for x in second.results])
   workspace+=sum(x.workspace not in q["scope"] for x in first.results);sensitive+=sum(x.sensitivity=="sensitive" and not q["filters"]["sensitive_allowed"] for x in first.results);forbidden+=sum(x.status in {"candidate","rejected","forgotten"} for x in first.results);provenance=provenance and all(x.provenance.journal_record_id and x.provenance.source_event_id and x.provenance.projection_hash for x in first.results)
 except BrainError as exc: report["failure_reasons"].append(f"{exc.code}: {exc.message}")
 after_journal,after_retrieval=sha(a.journal_db),sha(a.retrieval_db)
 report.update(retrieval_build_id=meta.get("build_id") if "meta" in locals() else None,embedding_model_fingerprint=meta.get("embedding_model") if "meta" in locals() else None,embedding_dimension=config.embedding_dimension,indexed_document_count=health.indexed_document_count if "health" in locals() else None,current_document_count=health.current_document_count if "health" in locals() else None,embedding_coverage=health.embedding_coverage if "health" in locals() else None,parity_count=sum(order_ok),top3_id_order_parity=all(order_ok) and len(order_ok)==24,contract_parity_count=sum(contract_ok),parity_mismatches=mismatches,workspace_leaks=workspace,sensitive_leaks=sensitive,forbidden_status_leaks=forbidden,deterministic=all(deterministic) and len(deterministic)==24,provenance_complete=provenance,journal_sha256_before=before_journal,journal_sha256_after=after_journal,retrieval_sha256_before=before_retrieval,retrieval_sha256_after=after_retrieval,journal_byte_identical=before_journal==after_journal,retrieval_byte_identical=before_retrieval==after_retrieval,p50_ms=percentile(lat,.5) if lat else None,p95_ms=percentile(lat,.95) if lat else None)
 if Path(a.parity_audit).is_file():
  audit=json.loads(Path(a.parity_audit).read_text()); mismatches=audit["mismatches"];report["candidate_and_score_parity"]=all(all(c["absolute_score_delta"] in (0.0,None) for c in m["candidates"]) for m in mismatches);report["top3_set_parity_vs_historical"]=all(not m["top3_membership_change"]["added_to_contract_top3"] and not m["top3_membership_change"]["removed_from_contract_top3"] for m in mismatches);report["historical_order_parity"]=not mismatches;report["contract_order_parity"]=all(contract_ok) and len(contract_ok)==24;report["contract_quality_metrics"]=json.loads(Path(a.contract_baseline).read_text())["metrics"];report["historical_quality_metrics"]={"top1_rate":.9166666666666666,"top3_rate":1.0,"noise_rate_top3":.10526315789473684};report["safety_gate_status"]=not any((workspace,sensitive,forbidden));report["contract_invariant_status"]=all([report["candidate_and_score_parity"],report["contract_order_parity"],report["safety_gate_status"],report["deterministic"],report["provenance_complete"],report["journal_byte_identical"],report["retrieval_byte_identical"],report["p95_ms"]<=50])
 checks=[report.get("contract_invariant_status",False),not report["failure_reasons"]];report["status"]="PASS" if all(checks) else "FAIL";Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2));return 0 if report["status"]=="PASS" else 1
if __name__=="__main__": raise SystemExit(main())
