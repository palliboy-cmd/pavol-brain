"""Explicit extraction of canonical record-to-record references.

This module is shared by bootstrap/audit code. It deliberately recognizes only
documented canonical fields plus typed ``record://`` URIs; it does not guess
references from arbitrary prose.
"""
import json


REFERENCE_FIELDS = {
    "source_record": "payload_source",
    "target_record_id": "payload_target",
    "old_record": "correction_old",
    "new_record": "correction_new",
    "supersedes": "supersedes",
    "superseded_by": "superseded_by",
}


def _walk(value, path="payload"):
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in REFERENCE_FIELDS and isinstance(item, str) and item:
                yield {"target_record": item, "field_path": child, "relation": REFERENCE_FIELDS[key]}
            yield from _walk(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    elif isinstance(value, str) and value.startswith("record://") and len(value) > 9:
        yield {"target_record": value[9:], "field_path": path, "relation": "record_uri"}


def payload_references(payload):
    if isinstance(payload, str):
        payload = json.loads(payload)
    return list(_walk(payload))


def journal_references(con):
    """Return every supported canonical record reference with its storage path."""
    references = []
    for record_id, workspace, payload in con.execute(
        "SELECT record_id,workspace,payload FROM memory_records ORDER BY record_id"
    ):
        for ref in payload_references(payload):
            references.append({"source_record": record_id, "source_workspace": workspace, **ref})
    for record_id, uri, relation in con.execute(
        "SELECT record_id,artifact_uri,relation FROM artifact_links WHERE artifact_uri LIKE 'record://%' ORDER BY record_id,artifact_uri,relation"
    ):
        references.append({"source_record": record_id, "source_workspace": None,
                           "target_record": uri[9:], "field_path": "artifact_links.artifact_uri",
                           "relation": relation})
    for record_id, supersedes, superseded_by in con.execute(
        "SELECT record_id,supersedes,superseded_by FROM record_state ORDER BY record_id"
    ):
        for field, target in (("supersedes", supersedes), ("superseded_by", superseded_by)):
            if target:
                references.append({"source_record": record_id, "source_workspace": None,
                                   "target_record": target, "field_path": f"record_state.{field}",
                                   "relation": field})
    for record_id, event_type, data in con.execute(
        "SELECT record_id,event_type,data FROM memory_events ORDER BY occurred_at,event_id"
    ):
        try:
            event_data = json.loads(data or "{}")
        except json.JSONDecodeError:
            continue
        for ref in _walk(event_data, "memory_events.data"):
            references.append({"source_record": record_id, "source_workspace": None,
                               **ref, "relation": f"event:{event_type}:{ref['relation']}"})
    return references
