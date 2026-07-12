from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class SearchRequest(ContractModel):
    query: str
    workspaces: list[str]
    types: list[Literal["decision","outcome","fact","preference","artifact_link","correction"]] | None = None
    mode: Literal["current","historical"] = "current"
    as_of: str | None = None
    sensitive_allowed: bool = False
    limit: int = Field(default=10, ge=1, le=50)
    include_artifacts: bool = True
    min_score: float | None = None
    request_id: str | None = None

class Provenance(ContractModel):
    journal_record_id: str
    source_event_id: str
    projection_hash: str
    supersedes: str | None = None
    superseded_by: str | None = None

class SearchResult(ContractModel):
    record_id: str; score: float; rank: int; workspace: str; type: str; sensitivity: str; status: str
    valid_at: str; invalid_at: str | None = None; is_current: bool; title: str; snippet: str
    provenance: Provenance; artifact_links: list[dict[str, Any]]; projection_hash: str
    embedding_model: str; retrieval_build_id: str

class SearchResponse(ContractModel):
    request_id: str; retrieval_build_id: str; embedding_model: str; mode: str
    degraded: bool = False; stale_index: bool | None = None; results: list[SearchResult]

class RecordEnvelope(ContractModel):
    record_id: str; type: str; workspace: str; sensitivity: str; payload: dict[str, Any]
    status: str; valid_at: str; invalid_at: str | None = None; supersedes: str | None = None; superseded_by: str | None = None

class RelatedResponse(ContractModel):
    request_id: str; record_id: str; related: list[dict[str, Any]]

class HealthReport(ContractModel):
    active_build_id: str | None; retrieval_db_available: bool; journal_available: bool
    indexed_document_count: int | None; current_document_count: int | None; embedding_coverage: float | None
    embedding_model: str | None; per_workspace_counts: dict[str, int]

class RebuildStatus(ContractModel):
    status: Literal["idle","ready","failed"]; active_build_id: str | None; last_known_build_metadata: dict[str, Any]

class BrainErrorModel(ContractModel):
    code: str; message: str; request_id: str; details: dict[str, Any] = Field(default_factory=dict)
