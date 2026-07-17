from brain import instance_identity
from .cursor import get_cursor
from .errors import RebuildRequired


def validate(con, journal_head, config):
    issues = []
    # Package 1 (closes B2): a soft, non-raising counterpart to the hard
    # enforce_retrieval() gate in ProjectionProjector._write() — this lets
    # read-only inspection (status()/plan()) surface instance drift as an
    # ordinary REBUILD_REQUIRED issue instead of only failing on the next
    # write attempt.
    marker_issue = instance_identity.diagnose_retrieval(con, getattr(config, "instance_id", "legacy"))
    if marker_issue: issues.append(marker_issue)
    cursor = get_cursor(con)
    if cursor:
        if cursor["projection_schema_version"] != config.projection_schema_version: issues.append("projection_schema_mismatch")
        if cursor["embedding_model_fingerprint"] != config.embedding_model_fingerprint: issues.append("embedding_model_mismatch")
        if cursor["embedding_dimension"] != config.embedding_dimension: issues.append("embedding_dimension_mismatch")
        if journal_head and cursor["last_source_event_id"] and cursor["last_source_event_id"] > journal_head: issues.append("cursor_ahead_of_journal")
    checks = {
        "orphan_embedding": "SELECT count(*) FROM retrieval_embeddings e LEFT JOIN retrieval_documents d USING(record_id) WHERE d.record_id IS NULL",
        "document_without_embedding": "SELECT count(*) FROM retrieval_documents d LEFT JOIN retrieval_embeddings e USING(record_id) WHERE e.record_id IS NULL",
        "forbidden_document": "SELECT count(*) FROM retrieval_documents WHERE status IN ('candidate','rejected','forgotten')",
        "hash_mismatch": "SELECT count(*) FROM retrieval_documents d JOIN retrieval_embeddings e USING(record_id) WHERE d.projection_hash != e.projection_hash",
        # Package 7 review F1: a document row can be deleted (e.g. by the
        # already_absent no-op path, or by out-of-band/FK-off corruption)
        # while its FTS row or link rows survive. Neither leaks through
        # search (both join to retrieval_documents), but both are silent
        # data debris that _assert_removed cannot always see (no doc_id is
        # known once the document row is already gone), so catch them here.
        "orphan_fts": "SELECT count(*) FROM retrieval_fts f LEFT JOIN retrieval_documents d ON d.doc_id = f.rowid WHERE d.doc_id IS NULL",
        "orphan_link": "SELECT count(*) FROM retrieval_document_links l LEFT JOIN retrieval_documents d USING(record_id) WHERE d.record_id IS NULL",
    }
    for name, query in checks.items():
        if con.execute(query).fetchone()[0]: issues.append(name)
    return issues


def require_healthy(con, journal_head, config):
    issues = validate(con, journal_head, config)
    if issues: raise RebuildRequired(",".join(issues))
    return issues
