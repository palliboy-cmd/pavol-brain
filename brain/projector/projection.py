"""One canonical text/projection definition shared with the baseline implementation."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "sqlite-spike" / "scripts"))
from fts_baseline import canonical_text  # noqa: E402 - existing normative baseline semantics

FORBIDDEN = {"candidate", "rejected", "forgotten"}


def active_relations(record):
    """Relations whose canonical validation fold is verified_active at build time."""
    states = record.get("artifact_validation") or {}
    return [{**relation, "validation": states[relation["artifact_link_id"]]}
            for relation in record.get("artifact_relations", [])
            if (states.get(relation["artifact_link_id"]) or {}).get("state") == "verified_active"]


def unresolved_relations(record):
    """Relations with no human-approved judgement. The projector never guesses
    these and never probes the filesystem; it must stop with REBUILD_REQUIRED."""
    if record["type"] != "artifact_link" or record["status"] in FORBIDDEN:
        return []
    states = record.get("artifact_validation") or {}
    return sorted(relation["artifact_link_id"] for relation in record.get("artifact_relations", [])
                  if (states.get(relation["artifact_link_id"]) or {"state": "unknown"}).get("state") == "unknown")


def needs_artifact_validation(record):
    return bool(unresolved_relations(record))


def eligible(record):
    if record["status"] in FORBIDDEN:
        return False
    if record["type"] == "artifact_link":
        # Canonical relation-level validation is the only eligibility evidence.
        # verified_inactive removes the relation; with no active relation left,
        # the artifact document is removed from the derived index.
        return record["status"] in {"accepted", "superseded"} and bool(active_relations(record))
    return record["status"] in {"accepted", "superseded"}


def document(record, source_event_id: str, schema_version: str):
    title, body, artifacts, text = canonical_text(record)
    if record["type"] == "artifact_link":
        links = [{"artifact_uri": relation["artifact_uri"], "relation": relation["relation"],
                  "confidence": relation["confidence"], "origin": "canonical_validation",
                  "verified_at": relation["validation"]["effective_at"]} for relation in active_relations(record)]
    else:
        links = record.get("artifact_links", [])
    fields = {
        "record_id": record["record_id"], "workspace": record["workspace"], "type": record["type"],
        "sensitivity": record["sensitivity"], "status": record["status"], "valid_at": record["valid_at"],
        "invalid_at": record.get("invalid_at"), "confidence": record["confidence"], "title": title,
        "body": body, "artifacts_text": artifacts, "canonical_text": text,
        "is_current": record["status"] == "accepted" and not record.get("invalid_at"),
        "source_event_id": source_event_id, "supersedes": record.get("supersedes"),
        "superseded_by": record.get("superseded_by"), "artifact_links": links,
    }
    # Slice 1's immutable contract baseline defines v1 as sha256(canonical_text).
    # The schema version is persisted in the cursor and validated there; changing
    # it is a rebuild boundary. Keeping this exact definition prevents a second,
    # incompatible hash universe during incremental adoption.
    fields["projection_hash"] = hashlib.sha256(text.encode()).hexdigest()
    return fields
