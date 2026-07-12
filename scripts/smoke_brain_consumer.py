#!/usr/bin/env python3
"""External-consumer smoke test; it imports only the public brain package."""
import argparse, hashlib, json, platform, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pydantic
import brain

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def timed(report,key,fn):
    start=time.perf_counter()
    try: value=fn(); report["operations"][key]={"pass":True,"latency_ms":(time.perf_counter()-start)*1000}; return value
    except Exception as exc: report["operations"][key]={"pass":False,"latency_ms":(time.perf_counter()-start)*1000,"error":getattr(exc,"code",type(exc).__name__)}; report["failure_reasons"].append(f"{key}: {getattr(exc,'code',type(exc).__name__)}"); return None
def main():
    p=argparse.ArgumentParser();p.add_argument("--journal-db",required=True);p.add_argument("--retrieval-db",required=True);p.add_argument("--output",required=True);a=p.parse_args()
    report={"timestamp":datetime.now(timezone.utc).isoformat(),"python_version":platform.python_version(),"pydantic_version":pydantic.__version__,"public_imports_used":["brain"],"operations":{},"failure_reasons":[]}
    before_j,before_r=sha(a.journal_db),sha(a.retrieval_db)
    client=brain.Brain(brain.BrainConfig(journal_db_path=Path(a.journal_db),retrieval_db_path=Path(a.retrieval_db)))
    health=timed(report,"health",client.health)
    if health: report["health_status"]={"retrieval_db_available":health.retrieval_db_available,"journal_available":health.journal_available,"active_build_id":health.active_build_id,"indexed_document_count":health.indexed_document_count,"current_document_count":health.current_document_count,"embedding_coverage":health.embedding_coverage,"embedding_model":health.embedding_model}
    current=timed(report,"current_search",lambda:client.search(query="Kde návrh oddeľuje audit od samotného vykonania dotazu?",workspaces=["ai-pos"],types=["decision"],limit=3))
    if current: report["current_search"]={"nonempty":bool(current.results),"rank_order":[x.rank for x in current.results],"provenance_complete":all(x.provenance.journal_record_id and x.provenance.source_event_id for x in current.results),"retrieval_build_id":current.retrieval_build_id,"degraded":current.degraded}
    cross=timed(report,"cross_workspace_search",lambda:client.search(query="Ktoré rozhodnutie zavádza query boundary a kde je README väzba?",workspaces=["ai-pos","ai-pos-app"],types=["decision","artifact_link"],limit=3))
    if cross: report["cross_workspace"]={"only_requested_workspaces":all(x.workspace in {"ai-pos","ai-pos-app"} for x in cross.results),"returned_workspaces":sorted({x.workspace for x in cross.results})}
    historical=timed(report,"historical_search",lambda:client.search(query="Aké staršie rozhodnutie o Graphiti patrí do histórie?",workspaces=["personal"],types=["decision"],mode="historical",limit=3))
    if historical: report["historical_search"]={"superseded_returned":any(x.status=="superseded" and not x.is_current and x.provenance.superseded_by for x in historical.results)}
    record=timed(report,"get_record",lambda:client.get_record(current.results[0].record_id)) if current and current.results else None
    if record: report["get_record"]={"same_record_id":record.record_id==current.results[0].record_id,"canonical_envelope":bool(record.payload),"status":record.status,"authority":"canonical_journal_envelope"}
    related=timed(report,"get_related",lambda:client.get_related("rec-045"))
    if related: report["get_related"]={"one_hop_explicit_only":bool(related.related) and all(x.get("relation") in {"supersedes","superseded_by","touches","implements","references"} for x in related.related),"count":len(related.related)}
    validation=timed(report,"validation_error",lambda:client.search(query="test",workspaces=["ai-pos"],min_score=.5))
    report["validation_error"]={"pass":validation is None and report["operations"]["validation_error"].get("error")=="BRAIN_FEATURE_NOT_ENABLED","code":report["operations"]["validation_error"].get("error")}
    if report["validation_error"]["pass"]: report["failure_reasons"].remove("validation_error: BRAIN_FEATURE_NOT_ENABLED")
    after_j,after_r=sha(a.journal_db),sha(a.retrieval_db)
    report.update(journal_sha256_before=before_j,journal_sha256_after=after_j,retrieval_sha256_before=before_r,retrieval_sha256_after=after_r,journal_byte_identical=before_j==after_j,retrieval_byte_identical=before_r==after_r)
    required=["health","current_search","cross_workspace_search","historical_search","get_record","get_related"]
    report["final_status"]="PASS" if all(report["operations"].get(x,{}).get("pass") for x in required) and report["validation_error"]["pass"] and report["journal_byte_identical"] and report["retrieval_byte_identical"] and report["current_search"]["nonempty"] and report["current_search"]["provenance_complete"] and report["cross_workspace"]["only_requested_workspaces"] and report["historical_search"]["superseded_returned"] and report["get_related"]["one_hop_explicit_only"] else "FAIL"
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(report,indent=2)+"\n");print(json.dumps(report,indent=2));return 0 if report["final_status"]=="PASS" else 1
if __name__=="__main__": raise SystemExit(main())
