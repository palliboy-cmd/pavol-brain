import json,uuid,urllib.request,time
from pathlib import Path
from .config import BrainConfig
from .errors import BrainError
from .models import *
from .policy import eligible,parse_as_of
from .ranking import rank,normalize
from .repository import Repository
from .audit import AuditLogger
from .runtime import RuntimeInspector
from .writer import JournalWriter
from .write_policy import validate_request_id, looks_like_secret

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
        self.audit=AuditLogger(self.config); self.writer=JournalWriter(self.config)
    def _request(self,**kwargs):
        validate_request_id(kwargs.get("request_id"))
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
        started=time.perf_counter(); validation_started=started
        try: req=self._request(**kwargs)
        except BrainError as exc:
            self.audit.write("search",request_id=exc.request_id,error_code=exc.code,total_latency_ms=round((time.perf_counter()-started)*1000,3));raise
        validation_ms=(time.perf_counter()-validation_started)*1000; embedding_started=time.perf_counter()
        try: vector=self.transport.embed(req.query)
        except BrainError as exc:
            self.audit.write("search",request_id=req.request_id,requested_workspaces=req.workspaces,resolved_workspaces=req.workspaces,error_code=exc.code,validation_latency_ms=round(validation_ms,3),total_latency_ms=round((time.perf_counter()-started)*1000,3));raise
        embedding_ms=(time.perf_counter()-embedding_started)*1000; retrieval_started=time.perf_counter()
        rows=[r for r in self.repository.candidates(req) if eligible(r,req)]; meta=self._meta()
        if self.config.embedding_dimension and len(vector)!=self.config.embedding_dimension: raise BrainError("BRAIN_MODEL_MISMATCH","query embedding dimension mismatch",req.request_id)
        try: vector=normalize(vector)
        except ValueError: raise BrainError("BRAIN_MODEL_MISMATCH","query embedding is invalid",req.request_id)
        results=[]
        for n,(score,row) in enumerate(rank(rows,vector)[:req.limit],1):
            journal=self.repository.journal_row(row["record_id"])
            if not journal: raise BrainError("BRAIN_PROVENANCE_CORRUPT","journal provenance is missing",req.request_id,{"record_id":row["record_id"]})
            event=journal.get("updated_event_id")
            if not event: raise BrainError("BRAIN_PROVENANCE_CORRUPT","source event is missing",req.request_id,{"record_id":row["record_id"]})
            links=self._scope_related(self.repository.related(row["record_id"]),req.workspaces,req.sensitive_allowed,
                                      req.workspaces if req.sensitive_allowed else []) if req.include_artifacts else []
            title=row["title"]; results.append(SearchResult(record_id=row["record_id"],score=score,rank=n,workspace=row["workspace"],type=row["type"],sensitivity=row["sensitivity"],status=row["status"],valid_at=row["valid_at"],invalid_at=row["invalid_at"],is_current=row["status"]=="accepted" and not row["invalid_at"],title=title,snippet=title[:240],provenance=Provenance(journal_record_id=row["record_id"],source_event_id=event,projection_hash=row["projection_hash"],supersedes=journal.get("supersedes"),superseded_by=journal.get("superseded_by")),artifact_links=links,projection_hash=row["projection_hash"],embedding_model=meta["embedding_model"],retrieval_build_id=meta["build_id"]))
        response=SearchResponse(request_id=req.request_id,retrieval_build_id=meta["build_id"],embedding_model=meta["embedding_model"],mode=req.mode,stale_index=self.health().stale_index,results=results)
        self.audit.write("search",request_id=req.request_id,requested_workspaces=req.workspaces,resolved_workspaces=req.workspaces,types=req.types,mode=req.mode,as_of=req.as_of,limit=req.limit,active_build_id=meta["build_id"],stale_flag=response.stale_index,validation_latency_ms=round(validation_ms,3),embedding_latency_ms=round(embedding_ms,3),retrieval_latency_ms=round((time.perf_counter()-retrieval_started)*1000,3),total_latency_ms=round((time.perf_counter()-started)*1000,3),result_count=len(results),returned_record_ids=[r.record_id for r in results])
        return response
    def get_record(self,record_id,*,sensitive_allowed=False,allowed_workspaces=None,sensitive_workspaces=None,request_id=None):
        validate_request_id(request_id)
        started=time.perf_counter();request_id=request_id or "uuid4-compat:"+str(uuid.uuid4());row=self.repository.journal_row(record_id)
        if not row or row["status"] in {"candidate","rejected","forgotten"}:
            self.audit.write("get_record",request_id=request_id,error_code="BRAIN_RECORD_NOT_FOUND",returned_record_ids=[]);raise BrainError("BRAIN_RECORD_NOT_FOUND","record is not available",request_id)
        if allowed_workspaces is not None and row["workspace"] not in set(allowed_workspaces):
            self.audit.write("get_record",request_id=request_id,error_code="BRAIN_RECORD_NOT_FOUND",returned_record_ids=[]);raise BrainError("BRAIN_RECORD_NOT_FOUND","record is not available",request_id)
        if row["sensitivity"]=="sensitive" and (not sensitive_allowed or (sensitive_workspaces is not None and row["workspace"] not in set(sensitive_workspaces))):
            self.audit.write("get_record",request_id=request_id,error_code="BRAIN_SENSITIVE_SCOPE_DENIED",returned_record_ids=[]);raise BrainError("BRAIN_SENSITIVE_SCOPE_DENIED","sensitive record requires explicit permission",request_id)
        result=RecordEnvelope(record_id=record_id,type=row["type"],workspace=row["workspace"],sensitivity=row["sensitivity"],payload=json.loads(row["payload"]),status=row["status"],valid_at=row["valid_at"],invalid_at=row["invalid_at"],supersedes=row["supersedes"],superseded_by=row["superseded_by"])
        self.audit.write("get_record",request_id=request_id,resolved_workspaces=[row["workspace"]],result_count=1,returned_record_ids=[record_id],total_latency_ms=round((time.perf_counter()-started)*1000,3));return result
    def get_related(self,record_id,relation_types=None,request_id=None,*,sensitive_allowed=False,allowed_workspaces=None,sensitive_workspaces=None):
        validate_request_id(request_id)
        request_id=request_id or "uuid4-compat:"+str(uuid.uuid4());source=self.get_record(record_id,sensitive_allowed=sensitive_allowed,allowed_workspaces=allowed_workspaces,sensitive_workspaces=sensitive_workspaces,request_id=request_id)
        if allowed_workspaces is not None and source.workspace not in set(allowed_workspaces):raise BrainError("BRAIN_RECORD_NOT_FOUND","record is not available",request_id)
        rows=self.repository.related(record_id)
        if relation_types: rows=[r for r in rows if r["relation"] in relation_types]
        if allowed_workspaces is not None:rows=self._scope_related(rows,allowed_workspaces,sensitive_allowed,sensitive_workspaces)
        result=RelatedResponse(request_id=request_id,record_id=record_id,related=rows)
        self.audit.write("get_related",request_id=request_id,result_count=len(rows),returned_record_ids=[record_id]);return result
    def _scope_related(self,rows,allowed_workspaces,sensitive_allowed=False,sensitive_workspaces=None):
        filtered=[];allowed=set(allowed_workspaces);sensitive=set(sensitive_workspaces or [])
        for row in rows:
            target=row.get("record_id") if row.get("direction")=="incoming" or str(row.get("artifact_uri","")).startswith("record://") else None
            if target:
                linked=self.repository.journal_row(target)
                if not linked or linked["workspace"] not in allowed:continue
                if linked["sensitivity"]=="sensitive" and (not sensitive_allowed or linked["workspace"] not in sensitive):continue
            filtered.append(row)
        return filtered
    def _meta(self):
        if hasattr(self.repository,"meta"): return self.repository.meta
        with self.repository.retrieval() as con:
            raw=con.execute("SELECT value FROM retrieval_embedding_meta WHERE key='contract'").fetchone()
        contract=json.loads(raw[0]) if raw else {};return {"build_id":contract.get("build_id","baseline"),"embedding_model":contract.get("exact_model_identifier",contract.get("fingerprint",self.config.embedding_model)),"contract":contract}
    def health(self):
        report=HealthReport(**RuntimeInspector(self.config,self._meta).inspect())
        self.audit.write("health",active_build_id=report.active_build_id,stale_flag=report.stale_index,result_count=0)
        return report
    def rebuild_status(self):
        health=self.health(); meta=self._meta() if health.retrieval_db_available else {}
        status="failed" if not health.retrieval_db_available else "rebuild_required" if health.rebuild_required else "ready"
        result=RebuildStatus(status=status,active_build_id=health.active_build_id,current_build_id=health.active_build_id,last_known_build_metadata=meta,cursor_after=health.retrieval_cursor,last_run_finished=health.last_successful_projector_run,last_successful_validation=health.last_successful_projector_run)
        self.audit.write("rebuild_status",active_build_id=result.active_build_id,result_count=0);return result
    def _record(self,model,record_type,request=None,*,allowed_workspaces=None,request_id=None,**kwargs):
        validate_request_id(request_id)
        started=time.perf_counter();request_id=request_id or "uuid4-compat:"+str(uuid.uuid4())
        try:
            if isinstance(request,model):value=request
            elif request is None:value=model(**kwargs)
            elif isinstance(request,dict):value=model(**request,**kwargs)
            else:raise BrainError("BRAIN_INVALID_REQUEST","write request must be a mapping or typed request model",request_id)
            data=value.model_dump(mode="json")
            metadata={key:data.pop(key) for key in ("workspace","sensitivity","source_assertion","source_excerpt","source_ref","session_ref","valid_at","idempotency_key","supersedes","change_reason","links")}
            result=self.writer.record(record_type,data,metadata,request_id=request_id,agent_id=self.config.client_identity,allowed_workspaces=allowed_workspaces)
            self.audit.write("record_"+record_type,request_id=request_id,resolved_workspaces=[result.workspace],result_count=1,
                             returned_record_ids=[result.record_id],policy_band=result.policy_band,status=result.status,
                             idempotent=result.idempotent,instance_id=self.config.instance_id,
                             total_latency_ms=round((time.perf_counter()-started)*1000,3))
            return result
        except BrainError as exc:
            if not exc.request_id: exc.request_id=request_id
            self.audit.write("record_"+record_type,request_id=request_id,error_code=exc.code,instance_id=self.config.instance_id,
                             total_latency_ms=round((time.perf_counter()-started)*1000,3));raise
        except Exception as exc:
            from pydantic import ValidationError
            if isinstance(exc,ValidationError):
                # B6: pydantic's default rendering of a validation error echoes the
                # offending raw value (e.g. a rejected verification key), so a
                # secret-shaped value must never be forwarded into response details.
                text=str(exc)
                details={} if looks_like_secret(text) else {"validation":text}
                error=BrainError("BRAIN_INVALID_REQUEST","invalid write request",request_id,details)
                self.audit.write("record_"+record_type,request_id=request_id,error_code=error.code,instance_id=self.config.instance_id)
                raise error from None
            raise
    def record_outcome(self,request=None,**kwargs): return self._record(OutcomeRequest,"outcome",request,**kwargs)
    def record_decision(self,request=None,**kwargs): return self._record(DecisionRequest,"decision",request,**kwargs)
    def record_problem(self,request=None,**kwargs): return self._record(ProblemRequest,"problem",request,**kwargs)
    def record_analysis(self,request=None,**kwargs): return self._record(AnalysisRequest,"analysis",request,**kwargs)
