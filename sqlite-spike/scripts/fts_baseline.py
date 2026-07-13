#!/usr/bin/env python3
"""Disposable FTS5-only retrieval baseline; no canonical-journal writes."""
import hashlib, json, re, sqlite3, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SCHEMA=ROOT/'sqlite-spike/schema.sql'; DATASET=ROOT/'spike/dataset/records.jsonl'
JOURNAL_SCHEMA=ROOT/'spike/schema/journal.sql'
MANIFEST=ROOT/'sqlite-spike/dataset/queries.json'; DB=ROOT/'sqlite-spike/retrieval.db'
FORBIDDEN={'candidate','rejected','forgotten'}

def canonical_text(record):
    p=record['payload']; kind=record['type']
    if kind=='decision':
        title=p['statement']; body=f"Rationale: {p.get('rationale','')} Status: {p.get('decision_status','')}"
        if record.get('schema_version',1)>=2:
            body += f" Verdict: {p.get('verdict','')} Reason: {p.get('reason','')} Reopen when: {p.get('reopen_when') or ''} Alternatives: {json.dumps(p.get('alternatives',[]),sort_keys=True,ensure_ascii=False)} Evidence: {' '.join(p.get('evidence',[]))}"
    elif kind=='outcome':
        title=p['summary']; body=f"Changes: {'; '.join(p.get('changes',[]))}. Verification: {json.dumps(p.get('verification',{}),sort_keys=True)}"
        if record.get('schema_version',1)>=2:
            body += f" Open questions: {'; '.join(p.get('open_questions',[]))}. Commit: {p.get('commit') or ''}"
    elif kind=='problem': title=p['statement']; body=f"Impact: {p.get('impact','')} Evidence: {' '.join(p.get('evidence',[]))}"
    elif kind=='analysis': title=p['summary']; body=f"Findings: {'; '.join(p.get('findings',[]))}. Evidence: {' '.join(p.get('evidence',[]))}"
    elif kind=='fact': title=f"{p['subject']} {p['predicate']} {p['object']}"; body=f"Evidence: {p.get('evidence','')}"
    elif kind=='artifact_link': title=f"{p['relation']} {p['artifact_uri']}"; body=f"Source record: {p['source_record']}. Origin claim: {p.get('origin_claim','')}"
    else: raise ValueError(f'unsupported record type {kind}')
    evidence_artifacts=p.get('evidence',[]) if record.get('schema_version',1)>=2 and isinstance(p.get('evidence',[]),list) else []
    commit_artifact=[p['commit']] if record.get('schema_version',1)>=2 and p.get('commit') else []
    artifacts=' '.join([p.get('artifact_uri',''), *p.get('artifacts',[]), *evidence_artifacts, *commit_artifact])
    return title,body,artifacts,'\n'.join((title,body,artifacts)).strip()

def source_records(path=DATASET):
    """Materialize fixture inputs into an ephemeral canonical-journal snapshot.

    The retrieval DB is populated only from this journal-shaped source; neither
    the project's canonical database nor its input fixture is modified.
    """
    unique={}
    for index,line in enumerate(Path(path).read_text().splitlines()):
        r=json.loads(line); unique.setdefault(r['record_id'],(index,r))
    journal=sqlite3.connect(':memory:'); journal.row_factory=sqlite3.Row
    journal.executescript(JOURNAL_SCHEMA.read_text())
    base=datetime(2026,7,10,tzinfo=timezone.utc)
    for index,r in unique.values():
        state=r['expected']['status']; valid_at=(base+timedelta(seconds=index)).isoformat()
        confidence=1.0 if r['source_assertion']=='imported_curated' else .5
        payload=json.dumps(r['payload'],sort_keys=True,separators=(',',':'),ensure_ascii=False)
        digest=hashlib.sha256(payload.encode()).hexdigest()
        journal.execute('INSERT INTO memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(r['record_id'],1,r['type'],r['workspace'],r['sensitivity'],payload,payload,digest,r['idempotency_key'],'dataset',r['source_assertion'],None,None,None,confidence,valid_at,valid_at))
        journal.execute('INSERT INTO record_state VALUES (?,?,?,?,?,?,?,?,?,?,?)',(r['record_id'],state,'human_approved' if state in {'accepted','superseded'} else 'pending','2026-07-10T00:00:00+00:00' if state=='superseded' else None,None,'rec-046' if state=='superseded' else None,'synthetic correction' if state=='superseded' else None,'projected' if r['expected']['projection'] else 'none',None,None,f'fixture-{index}'))
    rows=[]
    for row in journal.execute('SELECT r.*,s.status,s.invalid_at FROM memory_records r JOIN record_state s USING(record_id) ORDER BY r.record_id'):
        rows.append({**dict(row),'payload':json.loads(row['payload']),'expected':unique[row['record_id']][1]['expected']})
    return rows

def eligible(record, mode):
    if record['status'] in FORBIDDEN or not record['expected']['projection']: return False
    if record['type']=='artifact_link' and record['expected']['artifact_validation']!='valid': return False
    return record['status']=='accepted' if mode=='current' else record['status'] in {'accepted','superseded'}

def rebuild(db=DB, records=None):
    db=Path(db); db.parent.mkdir(parents=True,exist_ok=True)
    if db.exists(): db.unlink()
    con=sqlite3.connect(db); con.row_factory=sqlite3.Row; con.executescript(SCHEMA.read_text())
    count=0
    seen=set()
    for r in records or source_records():
        if r['record_id'] in seen: continue
        seen.add(r['record_id'])
        # Keep historical superseded documents too; query mode decides visibility.
        if not eligible(r,'historical'): continue
        title,body,artifacts,text=canonical_text(r); digest=hashlib.sha256(text.encode()).hexdigest()
        con.execute('INSERT INTO retrieval_documents(record_id,workspace,type,sensitivity,status,valid_at,invalid_at,confidence,title,body,artifacts_text,canonical_text,projection_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (r['record_id'],r['workspace'],r['type'],r['sensitivity'],r['status'],r['valid_at'],r['invalid_at'],r['confidence'],title,body,artifacts,text,digest))
        rowid=con.execute('SELECT doc_id FROM retrieval_documents WHERE record_id=?',(r['record_id'],)).fetchone()[0]
        con.execute('INSERT INTO retrieval_fts(rowid,title,body,artifacts_text) VALUES (?,?,?,?)',(rowid,title,body,artifacts)); count+=1
    con.commit(); return {'documents':count,'db':str(db)}

def fts_tokens(text): return re.findall(r"[\w]+",text,flags=re.UNICODE)
def search(con, query, limit=3):
    con.row_factory=sqlite3.Row
    filters=query['filters']; mode=filters['mode']; tokens=fts_tokens(query['query'])
    if not tokens: return []
    match=' OR '.join(f'"{token.replace(chr(34), "")}"' for token in tokens)
    placeholders=','.join('?'*len(query['scope'])); typeholders=','.join('?'*len(filters['types']))
    statuses=['accepted'] if mode=='current' else ['accepted','superseded']
    statusholders=','.join('?'*len(statuses))
    sql=f'''SELECT d.*, bm25(retrieval_fts) AS bm25_score
      FROM retrieval_fts JOIN retrieval_documents d ON d.doc_id=retrieval_fts.rowid
      WHERE retrieval_fts MATCH ? AND d.workspace IN ({placeholders})
      AND d.type IN ({typeholders}) AND d.status IN ({statusholders})
      AND (? OR d.sensitivity='normal')
      ORDER BY bm25_score ASC, d.confidence DESC, d.valid_at DESC, d.record_id ASC LIMIT {int(limit)}'''
    params=[match,*query['scope'],*filters['types'],*statuses,1 if filters.get('sensitive_allowed') else 0]
    return [dict(x) for x in con.execute(sql,params)]

def percentile(values,p):
    if not values:return None
    values=sorted(values); return values[min(len(values)-1,int((len(values)-1)*p))]

def execute(db=DB, manifest=MANIFEST):
    con=sqlite3.connect(db); con.row_factory=sqlite3.Row; queries=json.loads(Path(manifest).read_text()); runs=[]
    for q in queries:
        start=time.perf_counter(); rows=search(con,q); elapsed=(time.perf_counter()-start)*1000
        returned=[]
        for rank,row in enumerate(rows,1):
            returned.append({'record_id':row['record_id'],'rank':rank,'bm25_score':row['bm25_score'],'workspace':row['workspace'],'type':row['type'],'sensitivity':row['sensitivity'],'status':row['status'],'valid_at':row['valid_at'],'invalid_at':row['invalid_at'],'provenance':{'route':'fts5','projection_hash':row['projection_hash'],'record_id':row['record_id']}})
        ids=[x['record_id'] for x in returned]; wanted=set(q['expected_top']); alternatives=set(q['allowed_alternatives'])
        required=wanted if 'cross_workspace' in q['tags'] else wanted
        top3=required.issubset(ids) if 'cross_workspace' in q['tags'] else bool((wanted|alternatives)&set(ids))
        top1=bool(ids and ids[0] in wanted|alternatives)
        leaks={'workspace':sum(x['workspace'] not in q['scope'] for x in returned),'sensitive':sum(x['sensitivity']=='sensitive' and not q['filters'].get('sensitive_allowed') for x in returned),'forbidden_status':sum(x['status'] in FORBIDDEN for x in returned)}
        runs.append({'query_id':q['id'],'query':q['query'],'scope':q['scope'],'filters':q['filters'],'expected_top':q['expected_top'],'allowed_alternatives':q['allowed_alternatives'],'returned':returned,'top1_pass':top1,'top3_pass':top3,'failure_condition':q['failure_condition'],'failure_condition_pass':not any(leaks.values()),'leaks':leaks,'latency_ms':elapsed,'tags':q['tags']})
    return runs

def evaluate(runs, not_evaluated=None):
    n=len(runs); lat=[x['latency_ms'] for x in runs]; allrows=[r for x in runs for r in x['returned']]
    multilingual=[x for x in runs if 'multilingual' in x['tags']]; historical=[x for x in runs if 'historical' in x['tags']]
    return {'queries':n,'top1_rate':sum(x['top1_pass'] for x in runs)/n,'top3_rate':sum(x['top3_pass'] for x in runs)/n,
      'workspace_leaks':sum(x['leaks']['workspace'] for x in runs),'sensitive_leaks':sum(x['leaks']['sensitive'] for x in runs),'forbidden_status_leaks':sum(x['leaks']['forbidden_status'] for x in runs),
      'latency_ms':{'p50':percentile(lat,.5),'p95':percentile(lat,.95)},
      'not_evaluated':not_evaluated if not_evaluated is not None else ['embeddings','cosine','rrf','rebuild_equivalence','noise_rate_manual'],
      'multilingual_top3_rate':sum(x['top3_pass'] for x in multilingual)/len(multilingual) if multilingual else None,
      'historical_top3_rate':sum(x['top3_pass'] for x in historical)/len(historical) if historical else None,
      'failed_queries':[x['query_id'] for x in runs if not(x['top3_pass'] and x['failure_condition_pass'])],
      'go_gates':{'S1_top3':sum(x['top3_pass'] for x in runs)/n>=.8,'S2_top1':sum(x['top1_pass'] for x in runs)/n>=.6,'S3_multilingual':(sum(x['top3_pass'] for x in multilingual)/len(multilingual)>=.7) if multilingual else None,'S5_workspace':not any(x['leaks']['workspace'] for x in runs),'S6_sensitive':not any(x['leaks']['sensitive'] for x in runs),'S7_forbidden':not any(x['leaks']['forbidden_status'] for x in runs),'S9_historical':(sum(x['top3_pass'] for x in historical)/len(historical)==1) if historical else None,'S13_latency':percentile(lat,.95)<500}}
