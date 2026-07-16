import json,sqlite3
from contextlib import contextmanager
from pathlib import Path
from .errors import BrainError
from . import instance_identity
from .record_uri import classify_record_uri, record_target_id, CANONICAL_RECORD_TARGET

class Repository:
    def __init__(self,config): self.config=config
    @contextmanager
    def _readonly(self,path,code,message):
        path=Path(path)
        if not path.is_file(): raise BrainError(code,message,"")
        try:
            con=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)
            con.row_factory=sqlite3.Row
            con.execute("PRAGMA query_only=ON")
        except sqlite3.Error as exc: raise BrainError(code,message,"",{"reason":type(exc).__name__}) from exc
        try:
            yield con
        finally:
            con.close()
    @contextmanager
    def retrieval(self):
        with self._readonly(self.config.retrieval_db_path,"BRAIN_INDEX_UNAVAILABLE","retrieval database is unavailable") as con:
            instance_identity.enforce_retrieval(con,self.config.instance_id,allow_stamp=False)
            yield con
    @contextmanager
    def journal(self):
        with self._readonly(self.config.journal_db_path,"BRAIN_INDEX_UNAVAILABLE","canonical journal is unavailable") as con:
            instance_identity.enforce_journal(con,self.config.instance_id)
            yield con
    def workspaces(self):
        with self.retrieval() as con:
            return {r[0] for r in con.execute("SELECT DISTINCT workspace FROM retrieval_documents")}
    def sensitive_workspaces(self,workspaces):
        marks=",".join("?"*len(workspaces))
        with self.retrieval() as con:
            return {r[0] for r in con.execute(f"SELECT DISTINCT workspace FROM retrieval_documents WHERE workspace IN ({marks}) AND sensitivity='sensitive'",workspaces)}
    def candidates(self,request):
        with self.retrieval() as con:
            states=["accepted"] if request.mode=="current" else ["accepted","superseded"]
            default_types=["problem","analysis","decision","outcome","fact","preference","artifact_link","correction"]
            ws=",".join("?"*len(request.workspaces));ty=",".join("?"*len(request.types or default_types));st=",".join("?"*len(states))
            sql=f"""SELECT d.*,e.vector,e.dimensions FROM retrieval_documents d JOIN retrieval_embeddings e USING(record_id)
            WHERE d.workspace IN ({ws}) AND d.type IN ({ty}) AND d.status IN ({st}) AND (? OR d.sensitivity='normal')"""
            return [dict(r) for r in con.execute(sql,[*request.workspaces,*(request.types or default_types),*states,1 if request.sensitive_allowed else 0])]
    def journal_row(self,record_id):
        with self.journal() as con:
            row=con.execute("SELECT r.*,s.status,s.invalid_at,s.supersedes,s.superseded_by,s.updated_event_id FROM memory_records r JOIN record_state s USING(record_id) WHERE r.record_id=?",(record_id,)).fetchone()
            return dict(row) if row else None
    def related(self,record_id):
        with self.journal() as con:
            out=[]
            for r in con.execute("SELECT relation,artifact_uri,record_id FROM artifact_links WHERE record_id=? AND active=1",(record_id,)):
                item=dict(r)
                if classify_record_uri(item["artifact_uri"])==CANONICAL_RECORD_TARGET:item["record_id"]=record_target_id(item["artifact_uri"])
                out.append(item)
            target="record://"+record_id
            for r in con.execute("SELECT relation,artifact_uri,record_id FROM artifact_links WHERE artifact_uri=? AND active=1",(target,)):
                item=dict(r);item["direction"]="incoming";out.append(item)
            row=con.execute("SELECT supersedes,superseded_by FROM record_state WHERE record_id=?",(record_id,)).fetchone()
            if row:
                for kind,target in (("supersedes",row["supersedes"]),("superseded_by",row["superseded_by"])):
                    if target: out.append({"relation":kind,"record_id":target})
            return out
