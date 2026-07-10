#!/usr/bin/env python3
"""Create and validate the audited SQLite retrieval benchmark manifest.

The Graphiti manifest is input evidence and is never modified.
"""
import copy
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_QUERIES = ROOT / "spike" / "dataset" / "queries.json"
SOURCE_RECORDS = ROOT / "spike" / "dataset" / "records.jsonl"
TARGET_QUERIES = ROOT / "sqlite-spike" / "dataset" / "queries.json"
TARGET_DIFF = ROOT / "sqlite-spike" / "dataset" / "benchmark-manifest-diff.json"
TARGET_REPORT = ROOT / "sqlite-spike" / "results" / "benchmark-preflight.json"
EXPECTED_QUERY_COUNT = 24

# The inherited Graphiti fixture uses ``synthetic topic NNN`` placeholders.
# A placeholder carries no semantic evidence for its intended workspace, type,
# time mode, or expected result. These records may be structurally audited,
# but never contribute to relevance scoring until a curator supplies intent.
SEMANTIC_REVIEW_QUERY_IDS = {f"Q{number:02d}" for number in range(1, 25)}
SEMANTIC_REVIEW_FAILURE = (
    "semantic benchmark definition incomplete; exclude from scoring until query text, "
    "intended scope, and expected record are curated"
)


def load_records(path=SOURCE_RECORDS):
    """Keep the first submission for an ID: later exact retries are not new records."""
    records = {}
    for line in Path(path).read_text().splitlines():
        record = json.loads(line)
        records.setdefault(record["record_id"], record)
    return records


def issue(category, query_id, detail, record_id=None):
    return {"category": category, "query_id": query_id, "detail": detail, "record_id": record_id}


def validate_manifest(queries, records):
    issues = []
    ids = [query.get("id") for query in queries]
    for query_id, count in Counter(ids).items():
        if count > 1:
            issues.append(issue("duplicate_query_id", query_id, f"appears {count} times"))
    if len(ids) != EXPECTED_QUERY_COUNT:
        issues.append(issue("query_count", None, f"expected {EXPECTED_QUERY_COUNT}, got {len(ids)}"))

    for query in queries:
        query_id = query.get("id")
        if not query.get("failure_condition", "").strip():
            issues.append(issue("missing_failure_condition", query_id, "failure_condition is blank"))
        scope = query.get("scope") or []
        filters = query.get("filters") or {}
        mode = filters.get("mode", "current")
        for record_id in query.get("expected_top", []):
            record = records.get(record_id)
            if record is None:
                issues.append(issue("missing_record", query_id, "expected record does not exist", record_id))
                continue
            if record["workspace"] not in scope:
                issues.append(issue("workspace_mismatch", query_id, "expected record is outside declared scope", record_id))
            expected = record.get("expected", {})
            status = expected.get("status")
            projected = expected.get("projection")
            if mode == "current" and (status != "accepted" or projected is not True):
                issues.append(issue("ineligible_record", query_id, f"current mode cannot return {status!r}/projection={projected!r}", record_id))
            if mode == "historical" and status not in {"accepted", "superseded"}:
                issues.append(issue("ineligible_record", query_id, f"historical mode cannot return {status!r}", record_id))
            allowed_types = filters.get("types")
            if allowed_types is not None and record["type"] not in allowed_types:
                issues.append(issue("type_mismatch", query_id, "types filter excludes expected record", record_id))
            if record["sensitivity"] == "sensitive" and not filters.get("sensitive_allowed", False):
                issues.append(issue("sensitive_leak", query_id, "sensitive record lacks explicit sensitive_allowed", record_id))
            if mode in {"historical", "as_of"}:
                as_of = filters.get("as_of")
                if not as_of:
                    issues.append(issue("inconsistent_time_filter", query_id, "historical/as_of mode requires as_of", record_id))
                else:
                    try:
                        if datetime.fromisoformat(as_of) < datetime.fromisoformat(record["valid_at"]):
                            issues.append(issue("inconsistent_time_filter", query_id, "as_of predates expected record", record_id))
                    except ValueError:
                        issues.append(issue("inconsistent_time_filter", query_id, "as_of is not ISO-8601", record_id))
    return issues


def corrected_manifest(source_queries, records):
    corrected = copy.deepcopy(source_queries)
    changes = []
    for query in corrected:
        query_id = query["id"]
        expected_id = query["expected_top"][0]
        record = records[expected_id]
        desired_scope = [record["workspace"]]
        if query.get("scope") != desired_scope:
            changes.append({"query_id": query_id, "field": "scope", "old_value": query.get("scope"), "new_value": desired_scope, "reason": "expected record must be inside explicit retrieval scope", "evidence_record_id": expected_id})
            query["scope"] = desired_scope
        if query_id in SEMANTIC_REVIEW_QUERY_IDS:
            tags = list(query.get("tags", []))
            if "requires-semantic-review" not in tags:
                reviewed_tags = tags + ["requires-semantic-review"]
                changes.append({"query_id": query_id, "field": "tags", "old_value": query.get("tags"), "new_value": reviewed_tags, "reason": "placeholder query text has no semantic evidence for scope or expected result", "evidence_record_id": expected_id})
                query["tags"] = reviewed_tags
            if query.get("failure_condition") != SEMANTIC_REVIEW_FAILURE:
                changes.append({"query_id": query_id, "field": "failure_condition", "old_value": query.get("failure_condition"), "new_value": SEMANTIC_REVIEW_FAILURE, "reason": "prevent a structurally corrected placeholder from being scored as a semantic benchmark", "evidence_record_id": expected_id})
                query["failure_condition"] = SEMANTIC_REVIEW_FAILURE
    return corrected, changes


def run_preflight(source_queries, records):
    before = validate_manifest(source_queries, records)
    corrected, changes = corrected_manifest(source_queries, records)
    after = validate_manifest(corrected, records)
    report = {
        "total_queries": len(source_queries),
        "valid_before_correction": len(source_queries) - len({x["query_id"] for x in before if x["query_id"]}),
        "invalid_before_correction": len({x["query_id"] for x in before if x["query_id"]}),
        "corrected_queries": sorted({x["query_id"] for x in changes}),
        "valid_after_correction": len(corrected) - len({x["query_id"] for x in after if x["query_id"]}),
        "score_ready": False,
        "score_ready_queries": [q["id"] for q in corrected if q["id"] not in SEMANTIC_REVIEW_QUERY_IDS],
        "semantic_review_queries": sorted(SEMANTIC_REVIEW_QUERY_IDS),
        "issues_before_by_category": dict(Counter(x["category"] for x in before)),
        "issues_after_by_category": dict(Counter(x["category"] for x in after)),
        "issues_before": before,
        "issues_after": after,
    }
    if after or len(corrected) != EXPECTED_QUERY_COUNT or {q["id"] for q in corrected} != {q["id"] for q in source_queries}:
        raise ValueError(f"corrected manifest is invalid: {json.dumps(report, ensure_ascii=False)}")
    return corrected, changes, report


def main():
    records = load_records()
    source_queries = json.loads(SOURCE_QUERIES.read_text())
    corrected, changes, report = run_preflight(source_queries, records)
    TARGET_QUERIES.parent.mkdir(parents=True, exist_ok=True)
    TARGET_REPORT.parent.mkdir(parents=True, exist_ok=True)
    TARGET_QUERIES.write_text(json.dumps(corrected, ensure_ascii=False, indent=2) + "\n")
    TARGET_DIFF.write_text(json.dumps(changes, ensure_ascii=False, indent=2) + "\n")
    TARGET_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
