"""Thin, read-only MCP adapter over the public Brain contract."""
import os
from dataclasses import dataclass
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field, ValidationError

from .api import Brain
from .config import BrainConfig
from .errors import BrainError
from .models import SearchRequest


@dataclass(frozen=True)
class CapabilityPolicy:
    allowed_workspaces: frozenset[str]
    sensitive_grants: frozenset[str] = frozenset()
    profile: str = "default"

    @classmethod
    def from_env(cls):
        allowed = frozenset(x.strip() for x in os.getenv("BRAIN_ALLOWED_WORKSPACES", "").split(",") if x.strip())
        grants = frozenset(x.strip() for x in os.getenv("BRAIN_SENSITIVE_GRANTS", "").split(",") if x.strip())
        return cls(allowed, grants, os.getenv("BRAIN_CLIENT_PROFILE", "default"))

    def authorize(self, requested, sensitive_allowed=False, request_id=""):
        requested = set(requested)
        denied = requested - self.allowed_workspaces
        if denied: raise BrainError("BRAIN_WORKSPACE_DENIED", "workspace is not granted to this server profile", request_id, {"workspaces": sorted(denied)})
        if sensitive_allowed and not requested <= self.sensitive_grants:
            raise BrainError("BRAIN_SENSITIVE_SCOPE_DENIED", "sensitive scope is not granted to this server profile", request_id, {"workspaces": sorted(requested - self.sensitive_grants)})


def _error(exc):
    if isinstance(exc, BrainError): return {"error": {"code": exc.code, "message": exc.message, "request_id": exc.request_id, "details": exc.details}}
    if isinstance(exc, ValidationError): return {"error": {"code": "BRAIN_INVALID_REQUEST", "message": "invalid request", "request_id": "", "details": {"validation": str(exc)}}}
    raise exc


def create_server(config=None, policy=None, brain=None):
    config = config or BrainConfig(); policy = policy or CapabilityPolicy.from_env(); brain = brain or Brain(config)
    mcp = FastMCP("Pavol-Brain", instructions="Read-only retrieval. Explicit workspace scope is mandatory; preserve provenance.", json_response=True)

    @mcp.tool(name="brain_search")
    def brain_search(query: str, workspaces: list[str], types: list[Literal["decision","outcome","fact","preference","artifact_link","correction"]] | None = None,
                     mode: Literal["current","historical"] = "current", as_of: str | None = None, sensitive_allowed: bool = False,
                     limit: Annotated[int, Field(ge=1, le=50)] = 10, include_artifacts: bool = True, min_score: float | None = None,
                     request_id: str | None = None) -> dict:
        """Semantic retrieval using the frozen Brain search contract."""
        try:
            request = SearchRequest(query=query, workspaces=workspaces, types=types, mode=mode, as_of=as_of,
                                    sensitive_allowed=sensitive_allowed, limit=limit, include_artifacts=include_artifacts,
                                    min_score=min_score, request_id=request_id)
            policy.authorize(request.workspaces, request.sensitive_allowed, request.request_id or "")
            return brain.search(**request.model_dump()).model_dump(mode="json")
        except (BrainError, ValidationError) as exc: return _error(exc)

    @mcp.tool(name="brain_get_record")
    def brain_get_record(record_id: str, request_id: str | None = None) -> dict:
        """Return one record envelope when its workspace is granted."""
        try:
            result = brain.get_record(record_id, sensitive_allowed=False, request_id=request_id)
            policy.authorize([result.workspace], False, request_id or "")
            return result.model_dump(mode="json")
        except BrainError as exc: return _error(exc)

    @mcp.tool(name="brain_get_related")
    def brain_get_related(record_id: str, relation_types: list[str] | None = None, request_id: str | None = None) -> dict:
        """Return explicit links for a granted record."""
        try:
            record = brain.get_record(record_id, sensitive_allowed=False, request_id=request_id)
            policy.authorize([record.workspace], False, request_id or "")
            return brain.get_related(record_id, relation_types, request_id).model_dump(mode="json")
        except BrainError as exc: return _error(exc)

    @mcp.tool(name="brain_health")
    def brain_health() -> dict:
        """Return metadata-only runtime health."""
        return brain.health().model_dump(mode="json")

    @mcp.tool(name="brain_rebuild_status")
    def brain_rebuild_status() -> dict:
        """Return read-only projector/build status."""
        return brain.rebuild_status().model_dump(mode="json")

    return mcp


def main(): create_server().run(transport="stdio")
