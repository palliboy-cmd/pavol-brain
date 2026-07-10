"""Journal-to-Graphiti projection rules; Graphiti remains a disposable index."""
import json, time, uuid, sys
from datetime import datetime
from .journal import append_event
from .policy import projectable

EPISODE_TYPES={'decision','outcome','fact'}
def build_database(build_id): return 'spike_' + ''.join(c if c.isalnum() else '_' for c in build_id)
def eligible_records(con):
    rows=con.execute('''SELECT r.*,s.status,s.projection FROM memory_records r
                        JOIN record_state s ON s.record_id=r.record_id
                        ORDER BY r.record_id''').fetchall()
    return [row for row in rows if projectable(dict(row))]
def record_body(row):
    return json.dumps({'record_id':row['record_id'],'type':row['type'],'workspace':row['workspace'],
        'payload':json.loads(row['payload']),'status':row['status']},ensure_ascii=False,sort_keys=True)
def reference_time(row): return datetime.fromisoformat(row['valid_at'])
async def project_record(con, adapter, row, build_id):
    """Project once per (record, build); return a structured audit result."""
    existing=con.execute('SELECT episode_uuid FROM projection_map WHERE record_id=? AND build_id=?',(row['record_id'],build_id)).fetchone()
    if existing: return {'record_id':row['record_id'],'status':'skipped_idempotent','episode_uuid':existing['episode_uuid'],'latency_ms':0}
    started=time.perf_counter(); append_event(con,row['record_id'],'projection_started',{'build_id':build_id})
    try:
        # A previous crash may have persisted an episode before the SQLite cursor.
        await adapter.cleanup_episode_fragments(row['workspace'],f'record:{row["record_id"]}')
        episode_uuid=None
        if row['type'] in EPISODE_TYPES:
            print(f'episode_started {row["record_id"]}',file=sys.stderr)
            result=await adapter.add_episode(record_id=row['record_id'],build_id=build_id,group_id=row['workspace'],body=record_body(row),reference_time=reference_time(row))
            episode_uuid=result.episode.uuid
            print(f'episode_completed {row["record_id"]} {episode_uuid}',file=sys.stderr)
        # Deterministic edge is intentionally independent of LLM extraction.
        edge_uuid=str(uuid.uuid5(uuid.NAMESPACE_URL,f'{build_id}:edge:{row["record_id"]}'))
        target='artifact:' + row['record_id']
        _,_=await adapter.explicit_triplet(row['workspace'], 'record:'+row['record_id'], target,
            'ASSERTS', f'record_id={row["record_id"]}; {record_body(row)}', edge_uuid)
        con.execute('INSERT INTO projection_map(record_id,build_id,episode_uuid) VALUES (?,?,?)',(row['record_id'],build_id,episode_uuid))
        con.execute('INSERT OR IGNORE INTO graph_edges(edge_uuid,record_id,origin,build_id,created_at) VALUES (?,?,?,?,datetime("now"))',(edge_uuid,row['record_id'],'deterministic',build_id))
        append_event(con,row['record_id'],'projection_succeeded',{'build_id':build_id,'episode_uuid':episode_uuid,'edge_uuid':edge_uuid})
        con.commit()
        return {'record_id':row['record_id'],'status':'projected','episode_uuid':episode_uuid,'edge_uuid':edge_uuid,'latency_ms':(time.perf_counter()-started)*1000}
    except Exception as exc:
        append_event(con,row['record_id'],'projection_failed',{'build_id':build_id,'error':f'{type(exc).__name__}: {exc}' }); con.commit()
        return {'record_id':row['record_id'],'status':'failed','error':f'{type(exc).__name__}: {exc}','latency_ms':(time.perf_counter()-started)*1000}
