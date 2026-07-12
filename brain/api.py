import json,uuid,urllib.request
from pathlib import Path
from .config import BrainConfig
from .errors import BrainError
from .models import *
from .policy import eligible,parse_as_of
from .ranking import rank,normalize
from .repository import Repository

class HttpEmbeddingTransport:
    def __init__(self,config): self.config=config
    def embed(self,text):
        body=json.dumps({"model":self.config.embedding_model,"input":"search_query: "+text}).encode()
        try:
            req=urllib.request.Request(self.config.embedding_base_url.rstrip("/")+"/embeddings",data=body,headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req,timeout=self.config.timeout) as response: data=json.loads(response.read())
            return data["data"][0]["embedding"]
        except Exception as exc: raise BrainError("BRAIN_EMBEDDING_UNAVAILABLE","embedding endpoint is unavailable","",{"reason":type(exc).__name__})

class Brain:
    def __init__(self,config=None,transport=None,repository=None):
        self.config=config or BrainConfig(); self.repository=repository or Repository(self.config); self.transport=transport or HttpEmbeddingTransport(self.config)
    def _request(self,**kwargs):
        # Python stdlib has UUIDv4 but no reliable UUIDv7 in supported runtimes.
        # The prefix makes this compatibility variant explicit in the contract.
        request_id=kwargs.get("request_id") or "uuid4-compat:"+str(uuid.uuid4());kwargs["request_id"]=request_id
        try: req=SearchRequest(**kwargs)
        except Exception as exc:
            text=str(exc)
            code="BRAIN_INVALID_REQUEST"
            if "types." in text: code="BRAIN_INVALID_TYPE"
            elif "mode" in text: code="BRAIN_INVALID_MODE"
            elif "limit" in text: code="BRAIN_INVALID_LIMIT"
            raise BrainError(code,"invalid search request",request_id,{"validation":text})
        if not req.query.strip(): raise BrainError("BRAIN_EMPTY_QUERY","query must not be empty",request_id)
        if len(req.query)>2000: raise BrainError("BRAIN_INVALID_REQUEST","query exceeds 2000 characters",request_id)
        if not req.workspaces or any(not x or x=="*" for x in req.workspaces): raise BrainError("BRAIN_UNKNOWN_WORKSPACE","workspaces must be explicit and non-empty",request_id)
        if req.as_of and req.mode!="historical": raise BrainError("BRAIN_INVALID_AS_OF","as_of requires historical mode",request_id)
        if req.as_of: parse_as_of(req.as_of,request_id)
        if req.min_score is not None: raise BrainError("BRAIN_FEATURE_NOT_ENABLED","min_score is disabled before Slice 5",request_id)
        unknown=set(req.workspaces)-self.repository.workspaces()
        if unknown: raise BrainError("BRAIN_UNKNOWN_WORKSPACE","unknown workspace",request_id,{"workspaces":sorted(unknown)})
        sensitive=self.repository.sensitive_workspaces(req.workspaces)
        if sensitive and not req.sensitive_allowed: raise BrainError("BRAIN_SENSITIVE_SCOPE_DENIED","sensitive scope requires explicit permission",request_id,{"workspaces":sorted(sensitive)})
        return req
    def search(self,**kwargs):
        req=self._request(**kwargs)
        vector=self.transport.embed(req.query)
        rows=[r for r in self.repository.candidates(req) if eligible(r,req)]
        meta=self._meta()
        if self.config.embedding_dimension and len(vector)!=self.config.embedding_dimension: raise BrainError("BRAIN_MODEL_MISMATCH","query embedding dimension mismatch",req.request_id)
        try: vector=normalize(vector)
        except ValueError: raise BrainError("BRAIN_MODEL_MISMATCH","query embedding is invalid",req.request_id)
        results=[]
        for n,(score,row) in enumerate(rank(rows,vector)[:req.limit],1):
            journal=self.repository.journal_row(row["record_id"])
            if not journal: raise BrainError("BRAIN_PROVENANCE_CORRUPT","journal provenance is missing",req.request_id,{"record_id":row["record_id"]})
            event=journal.get("updated_event_id")
            if not event: raise BrainError("BRAIN_PROVENANCE_CORRUPT","source event is missing",req.request_id,{"record_id":row["record_id"]})
            title=row["title"]; results.append(SearchResult(record_id=row["record_id"],score=score,rank=n,workspace=row["workspace"],type=row["type"],sensitivity=row["sensitivity"],status=row["status"],valid_at=row["valid_at"],invalid_at=row["invalid_at"],is_current=row["status"]=="accepted" and not row["invalid_at"],title=title,snippet=title[:240],provenance=Provenance(journal_record_id=row["record_id"],source_event_id=event,projection_hash=row["projection_hash"],supersedes=journal.get("supersedes"),superseded_by=journal.get("superseded_by")),artifact_links=self.repository.related(row["record_id"]) if req.include_artifacts else [],projection_hash=row["projection_hash"],embedding_model=meta["embedding_model"],retrieval_build_id=meta["build_id"]))
        return SearchResponse(request_id=req.request_id,retrieval_build_id=meta["build_id"],embedding_model=meta["embedding_model"],mode=req.mode,results=results)
    def get_record(self,record_id,*,sensitive_allowed=False,request_id=None):
        request_id=request_id or "uuid4-compat:"+str(uuid.uuid4());row=self.repository.journal_row(record_id)
        if not row or row["status"] in {"candidate","rejected","forgotten"}: raise BrainError("BRAIN_RECORD_NOT_FOUND","record is not available",request_id)
        if row["sensitivity"]=="sensitive" and not sensitive_allowed: raise BrainError("BRAIN_SENSITIVE_SCOPE_DENIED","sensitive record requires explicit permission",request_id)
        return RecordEnvelope(record_id=record_id,type=row["type"],workspace=row["workspace"],sensitivity=row["sensitivity"],payload=json.loads(row["payload"]),status=row["status"],valid_at=row["valid_at"],invalid_at=row["invalid_at"],supersedes=row["supersedes"],superseded_by=row["superseded_by"])
    def get_related(self,record_id,relation_types=None,request_id=None):
        request_id=request_id or "uuid4-compat:"+str(uuid.uuid4());self.get_record(record_id,request_id=request_id); rows=self.repository.related(record_id)
        if relation_types: rows=[r for r in rows if r["relation"] in relation_types]
        return RelatedResponse(request_id=request_id,record_id=record_id,related=rows)
    def _meta(self):
        if hasattr(self.repository,"meta"): return self.repository.meta
        con=self.repository.retrieval();raw=con.execute("SELECT value FROM retrieval_embedding_meta WHERE key='contract'").fetchone()
        contract=json.loads(raw[0]) if raw else {};return {"build_id":"baseline","embedding_model":contract.get("fingerprint",self.config.embedding_model)}
    def health(self):
        retrieval=self.config.retrieval_db_path.exists(); journal=self.config.journal_db_path.exists()
        if not retrieval:return HealthReport(active_build_id=None,retrieval_db_available=False,journal_available=journal,indexed_document_count=None,current_document_count=None,embedding_coverage=None,embedding_model=None,per_workspace_counts={})
        con=self.repository.retrieval(); total=con.execute("SELECT count(*) FROM retrieval_documents").fetchone()[0]; current=con.execute("SELECT count(*) FROM retrieval_documents WHERE status='accepted'").fetchone()[0]; embedded=con.execute("SELECT count(*) FROM retrieval_embeddings").fetchone()[0]
        counts={r[0]:r[1] for r in con.execute("SELECT workspace,count(*) FROM retrieval_documents GROUP BY workspace")}
        meta=self._meta();return HealthReport(active_build_id=meta["build_id"],retrieval_db_available=True,journal_available=journal,indexed_document_count=total,current_document_count=current,embedding_coverage=embedded/total if total else 1.0,embedding_model=meta["embedding_model"],per_workspace_counts=counts)
    def rebuild_status(self):
        if not self.config.retrieval_db_path.exists(): return RebuildStatus(status="failed",active_build_id=None,last_known_build_metadata={})
        return RebuildStatus(status="ready",active_build_id=self._meta()["build_id"],last_known_build_metadata=self._meta())
