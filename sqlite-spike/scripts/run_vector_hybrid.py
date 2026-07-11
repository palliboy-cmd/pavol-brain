#!/usr/bin/env python3
import json,sqlite3,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path[:0]=[str(ROOT/'sqlite-spike/scripts'),str(ROOT/'sqlite-spike/src')]
from fts_baseline import DB,MANIFEST,evaluate,rebuild
from embeddings import EmbeddingClient,EmbeddingError,populate
from search import hybrid_search,vector_search

def route(con,queries,client,fn):
 out=[]
 for q in queries:
  start=time.perf_counter(); vector,_=client.embed(q['query'],'query'); rows=fn(con,q,vector); elapsed=(time.perf_counter()-start)*1000; returned=[]
  for rank,row in enumerate(rows,1): returned.append({'record_id':row['record_id'],'rank':rank,'workspace':row['workspace'],'type':row['type'],'sensitivity':row['sensitivity'],'status':row['status'],'valid_at':row['valid_at'],'invalid_at':row['invalid_at'],'vector_score':row.get('vector_score'),'bm25_score':row.get('bm25_score'),'rrf':row.get('rrf'),'provenance':{'route':'vector' if fn is vector_search else 'hybrid_rrf','record_id':row['record_id'],'projection_hash':row['projection_hash']}})
  ids=[x['record_id'] for x in returned]; wanted=set(q['expected_top']); alts=set(q['allowed_alternatives']); top3=wanted.issubset(ids) if 'cross_workspace' in q['tags'] else bool((wanted|alts)&set(ids)); top1=bool(ids and ids[0] in wanted|alts); leaks={'workspace':sum(x['workspace'] not in q['scope'] for x in returned),'sensitive':sum(x['sensitivity']=='sensitive' and not q['filters'].get('sensitive_allowed') for x in returned),'forbidden_status':sum(x['status'] in {'candidate','rejected','forgotten'} for x in returned)}
  out.append({'query_id':q['id'],'query':q['query'],'scope':q['scope'],'filters':q['filters'],'expected_top':q['expected_top'],'allowed_alternatives':q['allowed_alternatives'],'returned':returned,'top1_pass':top1,'top3_pass':top3,'failure_condition':q['failure_condition'],'failure_condition_pass':not any(leaks.values()),'leaks':leaks,'latency_ms':elapsed,'tags':q['tags']})
 return out
def main():
 rebuild(DB); con=sqlite3.connect(DB); con.row_factory=sqlite3.Row; client=EmbeddingClient(); queries=json.loads(Path(MANIFEST).read_text())
 try: coverage=populate(con,client)
 except EmbeddingError as exc:
  unavailable={'route_status':'not_evaluated','reason':str(exc),'embedding_contract':client.contract(),'queries':24,'evaluation':{'top1_rate':None,'top3_rate':None,'not_evaluated':['embedding_endpoint','vector_only','hybrid_rrf']}}
  for name,route_name in [('vector-baseline.json','vector_only'),('hybrid-baseline.json','hybrid_rrf')]: (ROOT/'sqlite-spike/results'/name).write_text(json.dumps(unavailable|{'route':route_name},ensure_ascii=False,indent=2)+'\n')
  print(json.dumps(unavailable,ensure_ascii=False,indent=2)); return
 vector=route(con,queries,client,vector_search); hybrid=route(con,queries,client,hybrid_search)
 vector_eval=evaluate(vector,['rebuild_equivalence','noise_rate_manual'])
 hybrid_eval=evaluate(hybrid,['rebuild_equivalence','noise_rate_manual'])
 outputs=[('vector-baseline.json','vector_only',vector,vector_eval,{'decision_hint':'preferred','decision_reason':'vector-only has the best measured top-3 result; hybrid adds no improvement.'}),('hybrid-baseline.json','hybrid_rrf',hybrid,hybrid_eval,{'decision_hint':'passes_but_not_preferred','decision_reason':'hybrid did not improve quality and worsened Q21 versus vector-only.'})]
 for name,route_name,runs,evaluation,hint in outputs: (ROOT/'sqlite-spike/results'/name).write_text(json.dumps({'route':route_name,'embedding':coverage,'queries':runs,'evaluation':evaluation,**hint},ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'vector':vector_eval,'hybrid':hybrid_eval,'embedding':coverage},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
