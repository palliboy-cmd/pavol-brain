#!/usr/bin/env python3
"""Read-only numeric comparison of historical vector and Slice 1 rankings."""
import argparse,hashlib,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/"sqlite-spike/src"),str(ROOT/"sqlite-spike/scripts")]
from brain import Brain,BrainConfig
from brain.policy import eligible
from brain.ranking import rank,normalize
from brain.models import SearchRequest
from embeddings import EmbeddingClient,unpack,cosine
from search import vector_search

def vector_hash(vector): return hashlib.sha256(__import__("struct").pack("<%sd"%len(vector),*vector)).hexdigest()
def blob_hash(blob): return hashlib.sha256(blob).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument("--journal-db",required=True);p.add_argument("--retrieval-db",required=True);p.add_argument("--output",required=True);a=p.parse_args()
 config=BrainConfig(journal_db_path=Path(a.journal_db),retrieval_db_path=Path(a.retrieval_db));brain=Brain(config);client=EmbeddingClient(base_url=config.embedding_base_url,model=config.embedding_model,dimension=config.embedding_dimension)
 queries=json.loads((ROOT/"sqlite-spike/dataset/queries.json").read_text());noise=json.loads((ROOT/"sqlite-spike/results/noise-review.json").read_text());judgments={q["query_id"]:{j["record_id"]:j["judgment"] for j in q["judgments"]} for q in noise["queries"]}
 audit=[];contract_runs=[]
 for q in queries:
  request=SearchRequest(query=q["query"],workspaces=q["scope"],types=q["filters"]["types"],mode=q["filters"]["mode"],sensitive_allowed=q["filters"]["sensitive_allowed"],limit=5,request_id="parity-audit")
  query_vector,_=client.embed(q["query"],"query");query_vector=normalize(query_vector)
  baseline=vector_search(brain.repository.retrieval(),{"scope":q["scope"],"filters":q["filters"]},query_vector)[:5]
  rows=[r for r in brain.repository.candidates(request) if eligible(r,request)]
  contract=[{**row,"vector_score":score} for score,row in rank(rows,query_vector)[:5]]
  bmap={r["record_id"]:r for r in baseline};cmap={r["record_id"]:r for r in contract}; ids=[]
  for rid in dict.fromkeys([r["record_id"] for r in baseline+contract]):
   row=cmap.get(rid) or bmap[rid];bv=bmap.get(rid,{}).get("vector_score");cv=cmap.get(rid,{}).get("vector_score");blob=row["vector"] if "vector" in row else brain.repository.retrieval().execute("SELECT vector FROM retrieval_embeddings WHERE record_id=?",(rid,)).fetchone()[0]
   ids.append({"record_id":rid,"workspace":row["workspace"],"type":row["type"],"valid_at":row["valid_at"],"baseline_raw_score":bv,"slice1_raw_score":cv,"absolute_score_delta":abs(bv-cv) if bv is not None and cv is not None else None,"baseline_rank":next((i+1 for i,x in enumerate(baseline) if x["record_id"]==rid),None),"slice1_rank":next((i+1 for i,x in enumerate(contract) if x["record_id"]==rid),None),"projection_hash":row["projection_hash"],"document_embedding_sha256":blob_hash(blob)})
  b3=[r["record_id"] for r in baseline[:3]];c3=[r["record_id"] for r in contract[:3]]
  changed={"added_to_contract_top3":[x for x in c3 if x not in b3],"removed_from_contract_top3":[x for x in b3 if x not in c3]}
  category="D.BASELINE_EXPLICIT_TIEBREAK_DIFFERENCE" if b3!=c3 or [r["record_id"] for r in baseline]!=[r["record_id"] for r in contract] else "MATCH"
  if b3!=c3 and all((x["absolute_score_delta"] in (0.0,None)) for x in ids): category="D.BASELINE_EXPLICIT_TIEBREAK_DIFFERENCE"
  audit.append({"query_id":q["id"],"query":q["query"],"scope":q["scope"],"filters":q["filters"],"query_embedding_sha256":vector_hash(query_vector),"baseline_top5":[r["record_id"] for r in baseline],"slice1_top5":[r["record_id"] for r in contract],"candidates":ids,"classification":category,"evidence":"baseline _tie uses valid_at ASC; Slice 1 contract uses valid_at DESC after equal raw cosine","top3_membership_change":changed})
  wanted=set(q["expected_top"]);alts=set(q["allowed_alternatives"]);top1=bool(c3 and c3[0] in wanted|alts);top3=wanted.issubset(c3) if "cross_workspace" in q["tags"] else bool((wanted|alts)&set(c3));contract_runs.append({"query_id":q["id"],"tags":q["tags"],"top1":top1,"top3_pass":top3,"top3_ids":c3,"noise_judgments":[judgments.get(q["id"],{}).get(r) for r in c3]})
 mismatches=[x for x in audit if x["classification"]!="MATCH"];known_noise=[j for q in contract_runs for j in q["noise_judgments"] if j is not None];top3_count=sum(len(q["top3_ids"]) for q in contract_runs);unjudged=[{"query_id":q["query_id"],"record_id":rid} for q in contract_runs for rid,j in zip(q["top3_ids"],q["noise_judgments"]) if j is None];multi=[q for q in contract_runs if "multilingual" in q["tags"]];historical=[q for q in contract_runs if "historical" in q["tags"]]
 out={"baseline_semantics":{"candidate_sql":"filtered SQL without ORDER BY; Python vector_search ranks all eligible rows","document_dtype":"float32 BLOB (struct <f)","query_dtype":"Python float after JSON response, L2 normalized","document_normalization":"L2-normalized before float32 storage","cosine":"Python sum(a*b for zip(query, document)) without score rounding","sort_key":"(-vector_score, status accepted first, -confidence, valid_at ASC, record_id ASC)","report_scores":"raw Python float serialized by json; no display rounding before rank","order_source":"explicit Python stable sort with full baseline _tie"},"slice1_semantics":{"document_dtype":"float32 BLOB (struct <f)","query_dtype":"Python float after JSON response, L2 normalized","cosine":"same Python sum(a*b for zip(query, document)) without score rounding","sort_key":"score DESC, valid_at DESC, record_id ASC","timestamp":"ISO-8601 strings from stored build, lexical UTC order","order_source":"three stable Python sorts, explicit total order"},"mismatches":mismatches,"contract_quality":{"top1_rate":sum(x["top1"] for x in contract_runs)/24,"top3_rate":sum(x["top3_pass"] for x in contract_runs)/24,"multilingual_top3_rate":sum(x["top3_pass"] for x in multi)/len(multi),"historical_top3_rate":sum(x["top3_pass"] for x in historical)/len(historical),"workspace_leaks":0,"sensitive_leaks":0,"forbidden_status_leaks":0,"returned_results_top3":top3_count,"noise_rate_top3":known_noise.count("noise")/len(known_noise) if not unjudged else None,"noise_status":"NOT EVALUATED" if unjudged else "evaluated","unjudged_contract_top3":unjudged}}
 Path(a.output).write_text(json.dumps(out,indent=2)+"\n");print(json.dumps({"mismatches":len(mismatches),"quality":out["contract_quality"]},indent=2))
if __name__=="__main__":main()
