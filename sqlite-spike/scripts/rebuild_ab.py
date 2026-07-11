#!/usr/bin/env python3
"""Compare two fresh FTS/vector builds from the identical journal snapshot."""
import json, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path[:0]=[str(ROOT/'sqlite-spike/scripts'),str(ROOT/'sqlite-spike/src')]
from embeddings import EmbeddingClient,EmbeddingError,populate
from fts_baseline import MANIFEST,rebuild
from run_vector_hybrid import route
from search import hybrid_search,vector_search

def snapshot(path):
 con=sqlite3.connect(path); con.row_factory=sqlite3.Row
 docs=[tuple(x) for x in con.execute('SELECT record_id,projection_hash FROM retrieval_documents ORDER BY record_id')]
 vectors=[tuple(x) for x in con.execute('SELECT record_id,model_fingerprint,projection_hash,vector FROM retrieval_embeddings ORDER BY record_id')]
 return docs,vectors
def outcome(rows): return [(x['query_id'],x['top1_pass'],x['top3_pass'],[(r['record_id'],r['rank']) for r in x['returned']]) for x in rows]
def set_active(control,build_id):
 status=control.execute('SELECT status FROM retrieval_builds WHERE build_id=?',(build_id,)).fetchone()
 if not status or status[0]!='ready': return False
 control.execute('INSERT OR REPLACE INTO retrieval_active_build VALUES (1,?)',(build_id,)); control.commit(); return True
def main():
 results=ROOT/'sqlite-spike/results'; a=results/'retrieval-build-a.db'; b=results/'retrieval-build-b.db'; control=sqlite3.connect(results/'rebuild-control.db')
 control.executescript('CREATE TABLE IF NOT EXISTS retrieval_builds(build_id TEXT PRIMARY KEY,status TEXT NOT NULL); CREATE TABLE IF NOT EXISTS retrieval_active_build(singleton INTEGER PRIMARY KEY CHECK(singleton=1),build_id TEXT NOT NULL);')
 client=EmbeddingClient(); queries=json.loads(Path(MANIFEST).read_text()); runs={}; coverage={}
 try:
  for build,path in [('build-a',a),('build-b',b)]:
   rebuild(path); con=sqlite3.connect(path); con.row_factory=sqlite3.Row; coverage[build]=populate(con,client); runs[build]={'vector':route(con,queries,client,vector_search),'hybrid':route(con,queries,client,hybrid_search)}; control.execute('INSERT OR REPLACE INTO retrieval_builds VALUES (?,?)',(build,'ready'))
 except EmbeddingError as exc:
  report={'status':'not_evaluated','reason':str(exc),'embedding_contract':client.contract(),'rebuild_equivalence':None,'active_build_switch':None}
  (results/'rebuild-ab.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(report,ensure_ascii=False,indent=2)); return
 failed='build-failed'; control.execute('INSERT OR REPLACE INTO retrieval_builds VALUES (?,?)',(failed,'failed')); control.commit()
 docs_a,vec_a=snapshot(a); docs_b,vec_b=snapshot(b)
 active_a=set_active(control,'build-a'); active_b=set_active(control,'build-b'); rejected_failed=not set_active(control,failed); active=control.execute('SELECT build_id FROM retrieval_active_build WHERE singleton=1').fetchone()[0]
 report={'rebuild_equivalence':{'projection_hashes':docs_a==docs_b,'document_counts':len(docs_a)==len(docs_b),'embedding_bytes':vec_a==vec_b,'vector_outcomes':outcome(runs['build-a']['vector'])==outcome(runs['build-b']['vector']),'hybrid_outcomes':outcome(runs['build-a']['hybrid'])==outcome(runs['build-b']['hybrid'])},'active_build_switch':{'build_a':active_a,'build_b':active_b,'failed_build_rejected':rejected_failed,'final_active_build':active},'coverage':coverage}
 report['rebuild_equivalence']['pass']=all(report['rebuild_equivalence'].values()); report['active_build_switch']['pass']=active_a and active_b and rejected_failed and active=='build-b'
 (results/'rebuild-ab.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+'\n'); print(json.dumps(report,ensure_ascii=False,indent=2,default=str))
if __name__=='__main__': main()
