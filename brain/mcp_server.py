"""Thin MCP adapter over the public Brain contract and profile policy."""
import os
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field, ValidationError

from .api import Brain
from .config import BrainConfig
from .errors import BrainError
from .models import SearchRequest, RecordLink, DecisionAlternative
from .control import ControlStore, RegistryPolicy
from .write_policy import validate_request_id


@dataclass(frozen=True)
class CapabilityPolicy:
    allowed_workspaces: frozenset[str]
    sensitive_grants: frozenset[str] = frozenset()
    profile: str = "default"
    write_enabled: bool = False

    @classmethod
    def from_env(cls):
        allowed = frozenset(x.strip() for x in os.getenv("BRAIN_ALLOWED_WORKSPACES", "").split(",") if x.strip())
        grants = frozenset(x.strip() for x in os.getenv("BRAIN_SENSITIVE_GRANTS", "").split(",") if x.strip())
        write_enabled=os.getenv("BRAIN_WRITE_ENABLED", "false").lower() in {"1","true","yes"}
        return cls(allowed, grants, os.getenv("BRAIN_CLIENT_PROFILE", "default"), write_enabled)

    def authorize(self, requested, sensitive_allowed=False, request_id="", tool="brain_search"):
        if tool in {"brain_record_outcome","brain_record_decision"} and not self.write_enabled:
            raise BrainError("BRAIN_WRITE_DISABLED", "write access is disabled for this server profile", request_id)
        requested = set(requested)
        denied = requested - self.allowed_workspaces
        if denied: raise BrainError("BRAIN_WORKSPACE_DENIED", "workspace is not granted to this server profile", request_id, {"workspaces": sorted(denied)})
        if sensitive_allowed and not requested <= self.sensitive_grants:
            raise BrainError("BRAIN_SENSITIVE_SCOPE_DENIED", "sensitive scope is not granted to this server profile", request_id, {"workspaces": sorted(requested - self.sensitive_grants)})

    def resolve_scope(self, requested=None, request_id="", tool="brain_search"):
        self.authorize([],False,request_id,tool)
        scope=set(self.allowed_workspaces) if requested is None else set(requested)
        denied=scope-set(self.allowed_workspaces)
        if denied:raise BrainError("BRAIN_WORKSPACE_DENIED","workspace is not granted to this server profile",request_id,{"workspaces":sorted(denied)})
        if not scope:raise BrainError("BRAIN_WORKSPACE_REQUIRED","profile has no default workspace scope",request_id)
        return sorted(scope),self


def _error(exc):
    if isinstance(exc, BrainError): return {"error": {"code": exc.code, "message": exc.message, "request_id": exc.request_id, "details": exc.details}}
    if isinstance(exc, ValidationError): return {"error": {"code": "BRAIN_INVALID_REQUEST", "message": "invalid request", "request_id": "", "details": {"validation": str(exc)}}}
    raise exc


def _effective_sensitivity(config, policy_profile, scope, requested):
    sensitive=set(getattr(policy_profile,"sensitive_workspace_grants",getattr(policy_profile,"sensitive_grants",frozenset())))
    return "sensitive" if requested=="sensitive" or config.instance_id=="work" or set(scope)&sensitive else "normal"


def create_server(config=None, policy=None, brain=None):
    config = config or getattr(brain,"config",None) or BrainConfig(); brain = brain or Brain(config)
    if policy is None and os.getenv("BRAIN_CONTROL_DB"):
        integration_id=os.getenv("BRAIN_INTEGRATION_ID","")
        policy=RegistryPolicy(ControlStore(os.environ["BRAIN_CONTROL_DB"]),integration_id,brain.audit,config.instance_id,config.client_identity)
    policy = policy or CapabilityPolicy.from_env()
    mcp = FastMCP("Pavol-Brain", instructions="Profile-scoped memory. Scope defaults to the profile and calls may only narrow it. Preserve provenance.", json_response=True)

    @mcp.tool(name="brain_search")
    def brain_search(query: str, workspaces: list[str] | None = None, types: list[Literal["problem","analysis","decision","outcome","fact","preference","artifact_link","correction"]] | None = None,
                     mode: Literal["current","historical"] = "current", as_of: str | None = None, sensitive_allowed: bool = False,
                     limit: Annotated[int, Field(ge=1, le=50)] = 10, include_artifacts: bool = True, min_score: float | None = None,
                     request_id: str | None = None) -> dict:
        """Semantic retrieval using the frozen Brain search contract."""
        try:
            validate_request_id(request_id)
            request_id = request_id or "uuid4-compat:" + str(uuid.uuid4())
            resolved,_=policy.resolve_scope(workspaces,request_id,tool="brain_search")
            request = SearchRequest(query=query, workspaces=resolved, types=types, mode=mode, as_of=as_of,
                                    sensitive_allowed=sensitive_allowed, limit=limit, include_artifacts=include_artifacts,
                                    min_score=min_score, request_id=request_id)
            policy.authorize(request.workspaces, request.sensitive_allowed, request.request_id or "",tool="brain_search")
            result=brain.search(**request.model_dump()).model_dump(mode="json")
            if hasattr(policy,"mark_real_call") and not config.audit_test_call:policy.mark_real_call()
            return result
        except (BrainError, ValidationError) as exc: return _error(exc)

    @mcp.tool(name="brain_get_record")
    def brain_get_record(record_id: str, request_id: str | None = None) -> dict:
        """Return one record envelope when its workspace is granted."""
        try:
            validate_request_id(request_id)
            scope,p=policy.resolve_scope(None,request_id or "",tool="brain_get_record")
            sensitive=set(getattr(p,"sensitive_workspace_grants",getattr(policy,"sensitive_grants",frozenset())))
            result = brain.get_record(record_id, sensitive_allowed=bool(sensitive),allowed_workspaces=scope,sensitive_workspaces=sensitive,request_id=request_id)
            return result.model_dump(mode="json")
        except BrainError as exc: return _error(exc)

    @mcp.tool(name="brain_get_related")
    def brain_get_related(record_id: str, relation_types: list[str] | None = None, request_id: str | None = None) -> dict:
        """Return explicit links for a granted record."""
        try:
            validate_request_id(request_id)
            scope,p=policy.resolve_scope(None,request_id or "",tool="brain_get_related")
            sensitive=set(getattr(p,"sensitive_workspace_grants",getattr(policy,"sensitive_grants",frozenset())))
            return brain.get_related(record_id, relation_types, request_id,sensitive_allowed=bool(sensitive),allowed_workspaces=scope,sensitive_workspaces=sensitive).model_dump(mode="json")
        except BrainError as exc: return _error(exc)

    @mcp.tool(name="brain_health")
    def brain_health() -> dict:
        """Return metadata-only runtime health."""
        try:
            policy.authorize([],False,"",tool="brain_health");return brain.health().model_dump(mode="json")
        except BrainError as exc:return _error(exc)

    @mcp.tool(name="brain_rebuild_status")
    def brain_rebuild_status() -> dict:
        """Return read-only projector/build status."""
        try:
            policy.authorize([],False,"",tool="brain_rebuild_status");return brain.rebuild_status().model_dump(mode="json")
        except BrainError as exc:return _error(exc)

    @mcp.tool(name="brain_record_outcome")
    def brain_record_outcome(summary: str, workspace: str | None = None, changes: list[str] | None = None,
                             # F2b: verification's *values* stay untyped (`Any`) on this raw FastMCP
                             # boundary on purpose. FastMCP builds its own pre-body pydantic model
                             # from this signature and raises a ToolError that embeds the raw dict
                             # key before the tool body -- and before this codebase's own
                             # sanitization -- ever runs, for a secret-shaped key paired with a
                             # non-string value. The domain model (OutcomeRequest.verification,
                             # brain/models.py) still requires `dict[VerificationKey, str]`; a
                             # non-string value is rejected inside the tool body via the same
                             # sanitized-error path as every other write validation failure (F1).
                             verification: dict[str,Any] | None = None, open_questions: list[str] | None = None,
                             artifacts: list[str] | None = None, commit: str | None = None,
                             source_assertion: Literal["explicit_user_command","explicit_user_confirmation","verified_tool_result","authoritative_document","agent_inference"] = "agent_inference",
                             source_excerpt: str | None = None, source_ref: str | None = None, session_ref: str | None = None,
                             sensitivity: Literal["normal","sensitive"] = "normal", valid_at: str | None = None,
                             idempotency_key: str | None = None, supersedes: str | None = None,
                             change_reason: str | None = None, links: list[RecordLink] | None = None,
                             request_id: str | None = None) -> dict:
        """Append a structured task outcome; profile write grant is required."""
        try:
            validate_request_id(request_id)
            request_id=request_id or "uuid4-compat:"+str(uuid.uuid4())
            scope,p=policy.resolve_scope([workspace] if workspace else None,request_id,tool="brain_record_outcome")
            if len(scope)!=1:raise BrainError("BRAIN_WORKSPACE_REQUIRED","write profile needs one default workspace or an explicit narrowing",request_id)
            sensitivity=_effective_sensitivity(config,p,scope,sensitivity)
            policy.authorize(scope,sensitivity=="sensitive",request_id,tool="brain_record_outcome")
            result=brain.record_outcome(summary=summary,workspace=scope[0],changes=changes or [],verification=verification or {},
                open_questions=open_questions or [],artifacts=artifacts or [],commit=commit,source_assertion=source_assertion,
                source_excerpt=source_excerpt,source_ref=source_ref,session_ref=session_ref,sensitivity=sensitivity,
                valid_at=valid_at,idempotency_key=idempotency_key,supersedes=supersedes,change_reason=change_reason,
                links=links or [],request_id=request_id,allowed_workspaces=scope)
            if hasattr(policy,"mark_real_call") and not config.audit_test_call:policy.mark_real_call()
            return result.model_dump(mode="json")
        except (BrainError,ValidationError) as exc:return _error(exc)

    @mcp.tool(name="brain_record_decision")
    def brain_record_decision(statement: str, rationale: str, reason: str, workspace: str | None = None,
                              alternatives: list[DecisionAlternative] | None = None, verdict: Literal["accepted","rejected","deferred"] = "accepted",
                              reopen_when: str | None = None, evidence: list[str] | None = None,
                              source_assertion: Literal["explicit_user_command","explicit_user_confirmation","verified_tool_result","authoritative_document","agent_inference"] = "agent_inference",
                              source_excerpt: str | None = None, source_ref: str | None = None, session_ref: str | None = None,
                              sensitivity: Literal["normal","sensitive"] = "normal", valid_at: str | None = None,
                              idempotency_key: str | None = None, supersedes: str | None = None,
                              change_reason: str | None = None, links: list[RecordLink] | None = None,
                              request_id: str | None = None) -> dict:
        """Append a confirmed or candidate decision with alternatives and reopen evidence."""
        try:
            validate_request_id(request_id)
            request_id=request_id or "uuid4-compat:"+str(uuid.uuid4())
            scope,p=policy.resolve_scope([workspace] if workspace else None,request_id,tool="brain_record_decision")
            if len(scope)!=1:raise BrainError("BRAIN_WORKSPACE_REQUIRED","write profile needs one default workspace or an explicit narrowing",request_id)
            sensitivity=_effective_sensitivity(config,p,scope,sensitivity)
            policy.authorize(scope,sensitivity=="sensitive",request_id,tool="brain_record_decision")
            result=brain.record_decision(statement=statement,rationale=rationale,reason=reason,workspace=scope[0],
                alternatives=alternatives or [],verdict=verdict,reopen_when=reopen_when,evidence=evidence or [],
                source_assertion=source_assertion,source_excerpt=source_excerpt,source_ref=source_ref,session_ref=session_ref,
                sensitivity=sensitivity,valid_at=valid_at,idempotency_key=idempotency_key,supersedes=supersedes,
                change_reason=change_reason,links=links or [],request_id=request_id,allowed_workspaces=scope)
            if hasattr(policy,"mark_real_call") and not config.audit_test_call:policy.mark_real_call()
            return result.model_dump(mode="json")
        except (BrainError,ValidationError) as exc:return _error(exc)

    return mcp


def main(): create_server().run(transport="stdio")
