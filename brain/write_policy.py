"""M1 review-first write policy (Proposal 002 bands A/B/C)."""
import math
import re
from collections import Counter

from .errors import BrainError

CONFIDENCE = {
    "explicit_user_command": 1.0,
    "explicit_user_confirmation": 1.0,
    "verified_tool_result": 0.95,
    "authoritative_document": 0.95,
    "agent_inference": 0.7,
    "imported_curated": 1.0,
}

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:sk|rk|pk)-(?:live|test|proj)?[-_A-Za-z0-9]{16,}\b", re.I),
    re.compile(r"\b(?:ghp_|github_pat_|xox[baprs]-|AKIA)[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)\b(?:password|passwd|api[_ -]?key|access[_ -]?token|client[_ -]?secret)\s*[:=]\s*[^\s,;]{6,}"),
)
DENY_TEXT_PATTERNS = (
    re.compile(r"(?im)^\s*(?:user|assistant|system|human|agent)\s*:\s+"),
    re.compile(r"(?i)\b(?:chain[- ]of[- ]thought|hidden reasoning|internal reasoning)\b"),
    re.compile(r"(?m)^Traceback \(most recent call last\):"),
)
URI_RE = re.compile(r"^(?:repo|git|adr|route|doc|workspace)://[^\s]+$")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

def _entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())

def looks_like_secret(value: str) -> bool:
    if any(pattern.search(value) for pattern in SECRET_PATTERNS):
        return True
    # URI punctuation must not combine an ordinary path plus a commit hash into
    # one entropy token. Individual path components are still scanned, so a
    # credential hidden in artifact metadata remains rejectable.
    sources=re.split(r"[:/]",value) if URI_RE.fullmatch(value) else [value]
    for source in sources:
     for token in re.findall(r"[A-Za-z0-9_+/=-]{32,}", source):
        if re.fullmatch(r"[0-9a-fA-F]{32,64}", token) or re.fullmatch(r"[0-9a-fA-F-]{36}", token):
            continue
        if _entropy(token) >= 4.25:
            return True
    return False

def collect_client_strings(value, values=None):
    """The one canonical scanner walk: dict keys and values, list/tuple
    elements, recursively. B6: a client-controlled string reachable from any
    persisted field must pass through here, whether it lives as a value or
    as a dict key at any nesting depth."""
    if values is None: values = []
    if isinstance(value, str): values.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            collect_client_strings(key, values)
            collect_client_strings(item, values)
    elif isinstance(value, (list, tuple)):
        for item in value: collect_client_strings(item, values)
    return values

def enforce_band_c(payload, provenance, request_id):
    values = collect_client_strings(payload); collect_client_strings(provenance, values)
    if any(looks_like_secret(value) for value in values):
        raise BrainError("BRAIN_WRITE_SECRET_REJECTED", "write rejected by secret filter", request_id)
    if any(pattern.search(value) for value in values for pattern in DENY_TEXT_PATTERNS):
        raise BrainError("BRAIN_WRITE_CONTENT_REJECTED", "transcripts, chain-of-thought, and raw stack traces are not accepted", request_id)

def validate_request_id(request_id):
    """B7: reject a malformed request_id before any journal work or audit
    write. The rejection always carries an empty request_id of its own so
    the invalid client value is never echoed back or logged."""
    if request_id is not None and not REQUEST_ID_RE.fullmatch(request_id):
        raise BrainError("BRAIN_INVALID_REQUEST", "request_id has an invalid shape", "")

def validate_evidence_uris(values, request_id, field="evidence"):
    invalid = sorted(value for value in values if not URI_RE.fullmatch(value))
    if invalid:
        raise BrainError("BRAIN_INVALID_ARTIFACT_URI", f"{field} must contain typed URIs", request_id, {"values": invalid})

def classify(record_type, payload, assertion, source_ref, request_id, artifact_results=None):
    if assertion == "authoritative_document" and not source_ref:
        raise BrainError("BRAIN_SOURCE_REF_REQUIRED", "authoritative_document requires source_ref", request_id)
    if assertion == "agent_inference":
        return "B", "candidate", "pending", CONFIDENCE[assertion]
    if record_type == "decision" and assertion not in {"explicit_user_command", "explicit_user_confirmation", "authoritative_document", "imported_curated"}:
        return "B", "candidate", "pending", CONFIDENCE[assertion]
    if record_type == "outcome" and assertion == "verified_tool_result":
        claimed=list(payload.get("artifacts",[]))+([payload["commit"]] if payload.get("commit") else [])
        if not claimed or not any((artifact_results or {}).get(uri,{}).get("valid") for uri in claimed):
            return "B", "candidate", "pending", CONFIDENCE[assertion]
    if record_type in {"problem", "analysis"} and assertion not in {"explicit_user_command", "explicit_user_confirmation", "authoritative_document", "imported_curated"}:
        return "B", "candidate", "pending", CONFIDENCE[assertion]
    review = "human_approved" if assertion == "imported_curated" else "auto_accepted"
    return "A", "accepted", review, CONFIDENCE[assertion]
