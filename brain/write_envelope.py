"""Canonical write-envelope field inventory (Package 4; locks spec §7.1).

Every field on every request model, plus the one control-adjacent field that
lives outside the pydantic models (``request_id``, see B7), is classified
into exactly one bucket below. ``tests/test_brain_write.py`` fails if a
request model gains a field that is not listed here -- adding an
unclassified field to the write path becomes impossible silently.
"""
from .models import (
    AnalysisRequest, DecisionAlternative, DecisionRequest, OutcomeRequest,
    ProblemRequest, RecordLink, SearchRequest,
)

SERVER_GENERATED = "server_generated"
TRUSTED_INTEGRATION_METADATA = "trusted_integration_metadata"
USER_CONTROLLED_CONTENT = "user_controlled_content"
SECURITY_SENSITIVE_CONTROL = "security_sensitive_control"

CLASSIFICATIONS = frozenset({
    SERVER_GENERATED, TRUSTED_INTEGRATION_METADATA,
    USER_CONTROLLED_CONTENT, SECURITY_SENSITIVE_CONTROL,
})

# Every request model whose fields must be inventoried. Response/internal
# models (WriteResponse, SearchResponse, ...) are out of scope: §7.1 only
# covers what a client can put on the wire.
REQUEST_MODELS = {
    "SearchRequest": SearchRequest,
    "OutcomeRequest": OutcomeRequest,
    "DecisionRequest": DecisionRequest,
    "ProblemRequest": ProblemRequest,
    "AnalysisRequest": AnalysisRequest,
    "DecisionAlternative": DecisionAlternative,
    "RecordLink": RecordLink,
}

_WRITE_METADATA = {
    "workspace": SECURITY_SENSITIVE_CONTROL,
    "sensitivity": SECURITY_SENSITIVE_CONTROL,
    "source_assertion": SECURITY_SENSITIVE_CONTROL,
    "source_excerpt": USER_CONTROLLED_CONTENT,
    "source_ref": USER_CONTROLLED_CONTENT,
    "session_ref": USER_CONTROLLED_CONTENT,
    "valid_at": SECURITY_SENSITIVE_CONTROL,
    "idempotency_key": SECURITY_SENSITIVE_CONTROL,
    "supersedes": SECURITY_SENSITIVE_CONTROL,
    "change_reason": SECURITY_SENSITIVE_CONTROL,
    "links": SECURITY_SENSITIVE_CONTROL,
}

FIELD_CLASSIFICATION = {
    "SearchRequest": {
        "query": USER_CONTROLLED_CONTENT,
        "workspaces": SECURITY_SENSITIVE_CONTROL,
        "types": USER_CONTROLLED_CONTENT,
        "mode": USER_CONTROLLED_CONTENT,
        "as_of": USER_CONTROLLED_CONTENT,
        "sensitive_allowed": SECURITY_SENSITIVE_CONTROL,
        "limit": USER_CONTROLLED_CONTENT,
        "include_artifacts": USER_CONTROLLED_CONTENT,
        "min_score": USER_CONTROLLED_CONTENT,
        "request_id": SECURITY_SENSITIVE_CONTROL,
    },
    "OutcomeRequest": {
        **_WRITE_METADATA,
        "summary": USER_CONTROLLED_CONTENT,
        "changes": USER_CONTROLLED_CONTENT,
        "verification": USER_CONTROLLED_CONTENT,
        "open_questions": USER_CONTROLLED_CONTENT,
        "artifacts": SECURITY_SENSITIVE_CONTROL,
        "commit": SECURITY_SENSITIVE_CONTROL,
    },
    "DecisionRequest": {
        **_WRITE_METADATA,
        "statement": USER_CONTROLLED_CONTENT,
        "rationale": USER_CONTROLLED_CONTENT,
        "alternatives": USER_CONTROLLED_CONTENT,
        "verdict": USER_CONTROLLED_CONTENT,
        "reason": USER_CONTROLLED_CONTENT,
        "reopen_when": USER_CONTROLLED_CONTENT,
        "evidence": SECURITY_SENSITIVE_CONTROL,
    },
    "ProblemRequest": {
        **_WRITE_METADATA,
        "statement": USER_CONTROLLED_CONTENT,
        "impact": USER_CONTROLLED_CONTENT,
        "evidence": SECURITY_SENSITIVE_CONTROL,
    },
    "AnalysisRequest": {
        **_WRITE_METADATA,
        "summary": USER_CONTROLLED_CONTENT,
        "findings": USER_CONTROLLED_CONTENT,
        "evidence": SECURITY_SENSITIVE_CONTROL,
    },
    "DecisionAlternative": {
        "option": USER_CONTROLLED_CONTENT,
        "verdict": USER_CONTROLLED_CONTENT,
        "reason": USER_CONTROLLED_CONTENT,
        "reopen_when": USER_CONTROLLED_CONTENT,
        "evidence": SECURITY_SENSITIVE_CONTROL,
    },
    "RecordLink": {
        "target_record_id": SECURITY_SENSITIVE_CONTROL,
        "relation": SECURITY_SENSITIVE_CONTROL,
    },
}

# request_id is deliberately kept out of every write-path pydantic model
# (B7): it travels as a plain keyword argument from the MCP boundary
# through brain/api.py::_record to brain/writer.py, and is asserted never
# to reach persistent storage. Classified here so the field-classification
# lock-in test still covers it even though no model carries it.
OUT_OF_BAND_FIELDS = {
    "request_id": SECURITY_SENSITIVE_CONTROL,
}
