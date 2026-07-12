#!/usr/bin/env python3
"""Generate immutable-style contract compatibility evidence without DB writes."""
import argparse,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/"sqlite-spike/src"),str(ROOT/"sqlite-spike/scripts")]
from brain import Brain,BrainConfig
from brain.models import SearchRequest
from brain.policy import eligible
from brain.ranking import rank,normalize
from embeddings import EmbeddingClient

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument("--journal-db",required=True);p.add_argument("--retrieval-db",required=True);p.add_argument("--output",required=True);a=p.parse_args()
 config=BrainConfig(journal_db_path=Path(a.journal_db),retrieval_db_path=Path(a.retrieval_db));brain=Brain(config);meta=brain._meta();client=EmbeddingClient(base_url=config.embedding_base_url,model=config.embedding_model,dimension=config.embedding_dimension)
 contract=client.contract()
 if contract["fingerprint"]!=meta["embedding_model"]: raise SystemExit("BRAIN_MODEL_MISMATCH: active retrieval fingerprint differs")
 queries=json.loads((ROOT/"sqlite-spike/dataset/queries.json").read_text());historical=ROOT/"sqlite-spike/results/vector-baseline.json";noise=json.loads((ROOT/"sqlite-spike/results/noise-review.json").read_text());judgments={q["query_id"]:{j["record_id"]:j["judgment"] for j in q["judgments"]} for q in noise["queries"]}
 results=[];top1=top3=workspace=sensitive=forbidden=0;noise_values=[];unjudged=[]
 for q in queries:
  req=SearchRequest(query=q["query"],workspaces=q["scope"],types=q["filters"]["types"],mode=q["filters"]["mode"],sensitive_allowed=q["filters"]["sensitive_allowed"],limit=50,request_id="contract-baseline-v1")
  vector,_=client.embed(q["query"],"query"); rows=[r for r in brain.repository.candidates(req) if eligible(r,req)];ordered=[{"record_id":row["record_id"],"rank":i,"raw_score":score,"workspace":row["workspace"],"type":row["type"],"status":row["status"],"valid_at":row["valid_at"],"projection_hash":row["projection_hash"]} for i,(score,row) in enumerate(rank(rows,normalize(vector)),1)]
  ids=[r["record_id"] for r in ordered[:3]];wanted=set(q["expected_top"]);alts=set(q["allowed_alternatives"]);hit1=bool(ids and ids[0] in wanted|alts);hit3=wanted.issubset(ids) if "cross_workspace" in q["tags"] else bool((wanted|alts)&set(ids));top1+=hit1;top3+=hit3
  for r in ordered[:3]:
   judgment=judgments.get(q["id"],{}).get(r["record_id"]); noise_values.append(judgment)
   if judgment is None: unjudged.append({"query_id":q["id"],"record_id":r["record_id"]})
   workspace+=r["workspace"] not in q["scope"];sensitive+=r["status"]=="sensitive" and not q["filters"]["sensitive_allowed"];forbidden+=r["status"] in {"candidate","rejected","forgotten"}
  results.append({"query_id":q["id"],"query":q["query"],"scope":q["scope"],"filters":q["filters"],"results":ordered,"top1_pass":hit1,"top3_pass":hit3})
 out={"schema":"vector-contract-baseline","version":"v1","generated_at":datetime.now(timezone.utc).isoformat(),"source_historical_baseline":{"path":"sqlite-spike/results/vector-baseline.json","sha256":sha(historical)},"retrieval_build_id":meta["build_id"],"model_fingerprint":meta["embedding_model"],"ranking_policy":"score DESC, valid_at DESC, record_id ASC","contract_compatibility_evidence":True,"historical_evidence_rewritten":False,"results":results,"metrics":{"top1_count":top1,"top1_rate":top1/24,"top3_count":top3,"top3_rate":top3/24,"workspace_leaks":workspace,"sensitive_leaks":sensitive,"forbidden_status_leaks":forbidden,"multilingual_top3_rate":sum(x["top3_pass"] for x in results if "multilingual" in next(q for q in queries if q["id"]==x["query_id"])["tags"])/3,"historical_top3_rate":sum(x["top3_pass"] for x in results if "historical" in next(q for q in queries if q["id"]==x["query_id"])["tags"]),"noise_count_top3":noise_values.count("noise") if not unjudged else None,"returned_results_top3":len(noise_values),"noise_rate_top3":noise_values.count("noise")/len(noise_values) if not unjudged else None,"noise_status":"NOT EVALUATED" if unjudged else "evaluated","unjudged":unjudged},"decision_hint":"contract_compatibility_evidence_only"}
 Path(a.output).write_text(json.dumps(out,indent=2)+"\n");print(json.dumps(out["metrics"],indent=2))
if __name__=="__main__":main()
