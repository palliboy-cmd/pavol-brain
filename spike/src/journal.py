import hashlib, json, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path
from .policy import CONFIDENCE, initial_state

def now(): return datetime.now(timezone.utc).isoformat()
def canonical(obj): return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
def new_id(prefix='rec'): return f'{prefix}_{uuid.uuid4().hex}'
def connect(path):
    con=sqlite3.connect(path); con.row_factory=sqlite3.Row; con.execute('PRAGMA foreign_keys=ON'); con.execute('PRAGMA journal_mode=WAL'); return con
def init(path):
    con=connect(path); con.executescript((Path(__file__).parents[1]/'schema/journal.sql').read_text()); return con
def fold(events):
    s={'status':'candidate','review':'pending','projection':'none','invalid_at':None,'supersedes':None,'superseded_by':None,'change_reason':None,'projection_error':None,'projected_build':None,'updated_event_id':None}
    for e in events:
        d=json.loads(e['data']); t=e['event_type']; s['updated_event_id']=e['event_id']
        if t=='record_created': s.update(status=d['status'],review=d['review'])
        elif t=='record_approved': s.update(status='accepted',review='human_approved')
        elif t=='record_rejected': s.update(status='rejected',review='rejected')
        elif t=='record_superseded': s.update(status='superseded',invalid_at=d.get('invalid_at'),superseded_by=d['superseded_by'],change_reason=d.get('reason'))
        elif t=='record_forgotten': s.update(status='forgotten',projection='removed')
        elif t=='projection_started': s.update(projection='pending',projection_error=None)
        elif t=='projection_succeeded': s.update(projection='projected',projected_build=d['build_id'])
        elif t=='projection_failed': s.update(projection='failed',projection_error=d.get('error'))
    return s
def append_event(con, record_id, event_type, data=None, actor='cli'):
    e={'event_id':new_id('evt'),'record_id':record_id,'event_type':event_type,'occurred_at':now(),'actor':actor,'data':canonical(data or {})}
    con.execute('INSERT INTO memory_events VALUES (:event_id,:record_id,:event_type,:occurred_at,:actor,:data)',e)
    st=fold(con.execute('SELECT * FROM memory_events WHERE record_id=? ORDER BY rowid',(record_id,)).fetchall())
    con.execute('INSERT OR REPLACE INTO record_state(record_id,status,review,invalid_at,supersedes,superseded_by,change_reason,projection,projection_error,projected_build,updated_event_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(record_id,*[st[k] for k in ('status','review','invalid_at','supersedes','superseded_by','change_reason','projection','projection_error','projected_build','updated_event_id')]))
    return e['event_id']
def insert(con,r):
    r=dict(r); r.setdefault('record_id',new_id()); r.setdefault('created_at',now()); r.setdefault('valid_at',r['created_at']); r.setdefault('agent_id','dataset'); r.setdefault('raw_input',r['payload']); r['confidence']=CONFIDENCE[r['source_assertion']]
    r['content_hash']=hashlib.sha256(canonical({'type':r['type'],'workspace':r['workspace'],'payload':r['payload']}).encode()).hexdigest(); r.setdefault('idempotency_key',r['content_hash'])
    fields=['record_id','schema_version','type','workspace','sensitivity','raw_input','payload','content_hash','idempotency_key','agent_id','source_assertion','source_excerpt','source_ref','session_ref','confidence','valid_at','created_at']; r.setdefault('schema_version',1); r.setdefault('source_excerpt',None); r.setdefault('source_ref',None); r.setdefault('session_ref',None)
    r['raw_input']=canonical(r['raw_input']); r['payload']=canonical(r['payload'])
    try: con.execute('INSERT INTO memory_records VALUES ('+','.join('?'*len(fields))+')',[r[x] for x in fields])
    except sqlite3.IntegrityError: return None
    status,review=initial_state({**r,'payload':json.loads(r['payload'])}); append_event(con,r['record_id'],'record_created',{'status':status,'review':review}); return r['record_id']
