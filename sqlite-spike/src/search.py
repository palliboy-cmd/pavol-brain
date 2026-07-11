"""Filtered exact cosine and deterministic FTS/vector RRF ranking."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from fts_baseline import fts_tokens, search as fts_search
from embeddings import cosine, unpack

def _tie(row): return (0 if row['status']=='accepted' else 1,-row['confidence'],row['valid_at'],row['record_id'])
def filtered(con,query):
    f=query['filters']; statuses=['accepted'] if f['mode']=='current' else ['accepted','superseded']; ws=','.join('?'*len(query['scope'])); ty=','.join('?'*len(f['types'])); st=','.join('?'*len(statuses))
    sql=f"SELECT * FROM retrieval_documents WHERE workspace IN ({ws}) AND type IN ({ty}) AND status IN ({st}) AND (? OR sensitivity='normal')"
    return [dict(x) for x in con.execute(sql,[*query['scope'],*f['types'],*statuses,1 if f.get('sensitive_allowed') else 0])]
def vector_search(con,query,query_vector):
    vectors={x['record_id']:unpack(x['vector'],x['dimensions']) for x in con.execute('SELECT record_id,vector,dimensions FROM retrieval_embeddings')}
    hits=[]
    for row in filtered(con,query):
        if row['record_id'] in vectors: hits.append({**row,'vector_score':cosine(query_vector,vectors[row['record_id']])})
    return sorted(hits,key=lambda x:(-x['vector_score'],*_tie(x)))[:30]
def hybrid_search(con,query,query_vector):
    fts=fts_search(con,query,limit=30); vec=vector_search(con,query,query_vector); merged={}
    for rank,row in enumerate(fts,1): merged.setdefault(row['record_id'],dict(row))['rank_fts']=rank
    for rank,row in enumerate(vec,1): merged.setdefault(row['record_id'],dict(row))['rank_vec']=rank
    for row in merged.values(): row['rrf']=sum(.5/(60+row[k]) for k in ('rank_fts','rank_vec') if k in row)
    return sorted(merged.values(),key=lambda x:(-x['rrf'],*_tie(x)))[:3]
