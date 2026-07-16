from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field

class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

# B6: a verification dict key is client-controlled and persisted (payload +
# raw_input) exactly like a value, so it gets the same shape floor as other
# short control-adjacent strings. This does not replace the Band C secret
# scan over keys (brain/write_policy.py::collect_client_strings) -- a
# key can have a valid shape and still be secret-shaped; both gates apply.
VerificationKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9 _./:-]{1,100}$")]

class SearchRequest(ContractModel):
    query: str
    workspaces: list[str] | None = None
    types: list[Literal["problem","analysis","decision","outcome","fact","preference","artifact_link","correction"]] | None = None
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
    journal_head_cursor: str | None = None
    retrieval_cursor: str | None = None
    cursor_gap_events: int | None = None
    oldest_unprojected_age_seconds: float | None = None
    embedding_count: int | None = None
    embedding_fingerprint: str | None = None
    embedding_dimension: int | None = None
    projection_schema_version: str | None = None
    last_successful_projector_run: str | None = None
    last_successful_full_rebuild: str | None = None
    last_failed_projector_run: str | None = None
    embedding_endpoint_status: Literal["available","unavailable","not_configured"] = "not_configured"
    endpoint_probe_latency_ms: float | None = None
    index_behind: bool | None = None
    stale_index: bool | None = None
    status: Literal["healthy","degraded","unavailable"] = "unavailable"
    rebuild_required: bool = False

class RebuildStatus(ContractModel):
    status: Literal["idle","running","ready","failed","rebuild_required"]
    active_build_id: str | None
    last_known_build_metadata: dict[str, Any]
    current_build_id: str | None = None
    last_run_started: str | None = None
    last_run_finished: str | None = None
    batch_counts: dict[str, int] = Field(default_factory=dict)
    cursor_before: str | None = None
    cursor_after: str | None = None
    last_error_code: str | None = None
    last_successful_validation: str | None = None
    previous_ready_build_id: str | None = None

class BrainErrorModel(ContractModel):
    code: str; message: str; request_id: str; details: dict[str, Any] = Field(default_factory=dict)

SourceAssertion = Literal[
    "explicit_user_command", "explicit_user_confirmation", "verified_tool_result",
    "authoritative_document", "agent_inference", "imported_curated",
]
RecordRelation = Literal["addresses", "analyzes", "decides", "implements", "results_in", "caused_by"]

class RecordLink(ContractModel):
    target_record_id: str
    relation: RecordRelation

class WriteMetadata(ContractModel):
    workspace: str | None = None
    sensitivity: Literal["normal", "sensitive"] = "normal"
    source_assertion: SourceAssertion = "agent_inference"
    source_excerpt: str | None = Field(default=None, max_length=500)
    source_ref: str | None = None
    session_ref: str | None = None
    valid_at: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)
    supersedes: str | None = None
    change_reason: str | None = None
    links: list[RecordLink] = Field(default_factory=list)

class OutcomeRequest(WriteMetadata):
    summary: str = Field(min_length=1, max_length=2000)
    changes: list[str] = Field(default_factory=list, max_length=100)
    verification: dict[VerificationKey, str] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list, max_length=100)
    artifacts: list[str] = Field(default_factory=list, max_length=100)
    commit: str | None = None

class DecisionAlternative(ContractModel):
    option: str = Field(min_length=1, max_length=2000)
    verdict: Literal["accepted", "rejected", "deferred"]
    reason: str = Field(min_length=1, max_length=4000)
    reopen_when: str | None = Field(default=None, max_length=4000)
    evidence: list[str] = Field(default_factory=list, max_length=100)

class DecisionRequest(WriteMetadata):
    statement: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=8000)
    alternatives: list[DecisionAlternative] = Field(default_factory=list, max_length=100)
    verdict: Literal["accepted", "rejected", "deferred"] = "accepted"
    reason: str = Field(min_length=1, max_length=4000)
    reopen_when: str | None = Field(default=None, max_length=4000)
    evidence: list[str] = Field(default_factory=list, max_length=100)

class ProblemRequest(WriteMetadata):
    statement: str = Field(min_length=1, max_length=4000)
    impact: str = Field(min_length=1, max_length=8000)
    evidence: list[str] = Field(default_factory=list, max_length=100)

class AnalysisRequest(WriteMetadata):
    summary: str = Field(min_length=1, max_length=4000)
    findings: list[str] = Field(min_length=1, max_length=100)
    evidence: list[str] = Field(default_factory=list, max_length=100)

class WriteResponse(ContractModel):
    request_id: str
    record_id: str
    event_id: str
    type: Literal["problem", "analysis", "decision", "outcome"]
    workspace: str
    status: Literal["accepted", "candidate"]
    review: Literal["auto_accepted", "human_approved", "pending"]
    policy_band: Literal["A", "B"]
    idempotent: bool
    created_at: str
    projection_pending: bool = True
