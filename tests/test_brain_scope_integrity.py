"""Package 5 (closes B4 + B5): scope-safe retrieval expansion.

B4 -- supersedes/superseded_by rows bypassed the read-side scope filter
because Brain._scope_related only recognized a target for incoming typed
links and record:// artifact URIs. B5 -- Brain.get_record/get_related
treated an omitted/None allowed_workspaces as unrestricted access.

These tests manually corrupt a temporary fixture journal with historical
pointers the write path itself would never create (cross-workspace
supersedes, dangling targets, rejected/forgotten targets, sensitive targets
without a grant) and assert the read API never surfaces them -- matching
the governing invariant (I10, spec S5.2): every id returned via expansion
must be independently fetchable by the same caller through a direct,
scoped get_record call.
"""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from brain.api import Brain, UNRESTRICTED_OPERATOR_SCOPE
from brain.config import BrainConfig
from brain.errors import BrainError
from brain.projector import ProjectorConfig, ProjectionProjector
from brain.projector.models import ProjectionStatus

from test_brain_write import brain, problem, NoopTransport, FakeEmbedder

CORRUPT_AT = "2026-07-13T00:00:00+00:00"


def corrupt_supersedes(journal, record_id, target_id):
    con = sqlite3.connect(journal)
    con.execute("UPDATE record_state SET supersedes=? WHERE record_id=?", (target_id, record_id))
    con.commit(); con.close()


def corrupt_superseded_by(journal, record_id, target_id):
    con = sqlite3.connect(journal)
    con.execute("UPDATE record_state SET superseded_by=? WHERE record_id=?", (target_id, record_id))
    con.commit(); con.close()


def corrupt_link(journal, source_id, target_uri, relation="addresses"):
    con = sqlite3.connect(journal)
    con.execute("INSERT INTO artifact_links VALUES (?,?,?,?,?,?,?)",
                (source_id, target_uri, relation, 1.0, "corrupt-fixture", CORRUPT_AT, 1))
    con.commit(); con.close()


def plant_record(journal, record_id, workspace, status="rejected", sensitivity="normal"):
    """Insert a bare memory_records + record_state row directly (mirrors
    journal_fixture's raw-insert style): the write path never produces a
    rejected/forgotten/sensitive-without-grant row shaped like this on
    purpose, so a corrupt/legacy journal is the only way it can occur."""
    con = sqlite3.connect(journal)
    payload = json.dumps({"statement": "corrupt fixture", "impact": "n/a"})
    digest = hashlib.sha256(payload.encode()).hexdigest()
    con.execute("INSERT INTO memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (record_id, 2, "problem", workspace, sensitivity, payload, payload, digest,
                 f"idem-{record_id}", "fixture", "explicit_user_confirmation", None, None, None,
                 1.0, CORRUPT_AT, CORRUPT_AT))
    con.execute("INSERT INTO record_state VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (record_id, status, "human_approved" if status == "accepted" else "pending",
                 None, None, None, None, "none", None, None, f"evt-{record_id}"))
    con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)",
                (f"evt-{record_id}", record_id, "record_created", CORRUPT_AT, "fixture", "{}"))
    con.commit(); con.close()


def seeded(tmp_path):
    b, journal = brain(tmp_path)
    # Distinct statement text per record: content_hash (writer.py) is
    # {type, workspace, payload} only -- same-workspace records with
    # identical payload text collide into the semantic-duplicate/candidate
    # path (writer.py:149-153), which would make them invisible regardless
    # of scope and defeat these fixtures.
    personal = b.record_problem(**problem(statement="Scoped source", workspace="personal", idempotency_key="scope-personal"))
    foreign = b.record_problem(**problem(statement="Foreign target", workspace="ai-pos", idempotency_key="scope-foreign"))
    sensitive = b.record_problem(**problem(statement="Sensitive same-workspace target", workspace="personal", sensitivity="sensitive", idempotency_key="scope-sensitive"))
    return b, journal, personal, foreign, sensitive


def project(journal, tmp_path, name="scope-retrieval.db"):
    retrieval = tmp_path / name
    projector = ProjectionProjector(ProjectorConfig(journal, retrieval, "fake", 4, "fake", instance_id="personal"), FakeEmbedder())
    while projector.run_once(100).status == ProjectionStatus.HEALTHY:
        pass
    return retrieval


def scoped_reader(journal, retrieval):
    return Brain(BrainConfig(journal_db_path=journal, retrieval_db_path=retrieval, embedding_dimension=4,
                              endpoint_probe_timeout=.01, client_identity="reader", instance_id="personal"),
                 NoopTransport())


# ---------------------------------------------------------------------------
# B5 -- fail-closed: an omitted scope must never mean unrestricted access.
# ---------------------------------------------------------------------------

def test_get_record_without_scope_fails_closed_before_reading_data(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    with pytest.raises(TypeError):
        b.get_record(personal.record_id)


def test_get_related_without_scope_fails_closed_before_reading_data(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    with pytest.raises(TypeError):
        b.get_related(personal.record_id)


class CountingTransport:
    def __init__(self): self.calls = 0
    def embed(self, text): self.calls += 1; return [1.0, 0.0, 0.0, 0.0]


def test_search_without_scope_fails_closed_before_embedding(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    counting = CountingTransport()
    b.transport = counting
    with pytest.raises(BrainError) as err:
        b.search(query="Scoped source")
    assert err.value.code == "BRAIN_UNKNOWN_WORKSPACE"
    assert counting.calls == 0


def test_explicit_scope_still_works(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    record = b.get_record(personal.record_id, allowed_workspaces=["personal"])
    assert record.record_id == personal.record_id
    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    assert related.record_id == personal.record_id


# ---------------------------------------------------------------------------
# B4 -- corrupt supersedes/superseded_by must not bypass the scope filter.
# ---------------------------------------------------------------------------

def test_b4_probe_corrupt_cross_workspace_supersedes_excluded_from_get_related(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    corrupt_supersedes(journal, personal.record_id, foreign.record_id)
    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    assert not any(row.get("record_id") == foreign.record_id for row in related.related)


def test_b4_probe_corrupt_supersedes_nulled_in_get_record_provenance(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    corrupt_supersedes(journal, personal.record_id, foreign.record_id)
    record = b.get_record(personal.record_id, allowed_workspaces=["personal"])
    assert record.supersedes is None
    # sanitization must not touch canonical journal state
    con = sqlite3.connect(journal)
    assert con.execute("SELECT supersedes FROM record_state WHERE record_id=?", (personal.record_id,)).fetchone()[0] == foreign.record_id
    con.close()


def test_b4_probe_corrupt_superseded_by_excluded_and_nulled(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    corrupt_superseded_by(journal, personal.record_id, foreign.record_id)
    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    assert not any(row.get("record_id") == foreign.record_id for row in related.related)
    record = b.get_record(personal.record_id, allowed_workspaces=["personal"])
    assert record.superseded_by is None


def test_b4_probe_include_artifacts_and_provenance_sanitized_in_search(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    corrupt_supersedes(journal, personal.record_id, foreign.record_id)
    corrupt_link(journal, personal.record_id, "record://" + foreign.record_id)
    retrieval = project(journal, tmp_path)
    reader = scoped_reader(journal, retrieval)
    # sensitive_allowed=True because the fixture's "personal" workspace also
    # holds a sensitive record (Repository.sensitive_workspaces gates the
    # whole request-level workspace, unrelated to Package 5); it does not
    # change the cross-workspace assertions below, which are workspace-only.
    result = reader.search(query="Scoped source", workspaces=["personal"], types=["problem"], limit=10,
                            include_artifacts=True, sensitive_allowed=True)
    row = next(item for item in result.results if item.record_id == personal.record_id)
    assert row.provenance.supersedes is None
    assert not any(link.get("record_id") == foreign.record_id for link in row.artifact_links)
    payload = json.dumps(result.model_dump(mode="json"))
    assert foreign.record_id not in payload


# ---------------------------------------------------------------------------
# Corrupt relation matrix (spec S10 rows 11-14): every shape the writer would
# never produce, and the read API's response to each.
# ---------------------------------------------------------------------------

def test_corrupt_incoming_link_from_foreign_workspace_excluded(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    corrupt_link(journal, foreign.record_id, "record://" + personal.record_id)
    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    assert not any(row.get("direction") == "incoming" and row.get("record_id") == foreign.record_id for row in related.related)


def test_corrupt_outgoing_link_to_foreign_workspace_excluded(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    corrupt_link(journal, personal.record_id, "record://" + foreign.record_id)
    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    assert not any(row.get("record_id") == foreign.record_id for row in related.related)


def test_dangling_supersedes_target_excluded(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    corrupt_supersedes(journal, personal.record_id, "rec-does-not-exist")
    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    assert not any(row.get("record_id") == "rec-does-not-exist" for row in related.related)
    record = b.get_record(personal.record_id, allowed_workspaces=["personal"])
    assert record.supersedes is None


@pytest.mark.parametrize("status", ["rejected", "forgotten"])
def test_supersedes_target_with_non_visible_status_excluded(tmp_path, status):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    plant_record(journal, "rec-hidden-status", "personal", status=status)
    corrupt_supersedes(journal, personal.record_id, "rec-hidden-status")
    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    assert not any(row.get("record_id") == "rec-hidden-status" for row in related.related)
    record = b.get_record(personal.record_id, allowed_workspaces=["personal"])
    assert record.supersedes is None
    # same outward behavior as a dangling / out-of-scope target: not found, no leak
    with pytest.raises(BrainError) as err:
        b.get_record("rec-hidden-status", allowed_workspaces=["personal"])
    assert err.value.code == "BRAIN_RECORD_NOT_FOUND"


def test_sensitive_target_without_grant_excluded_same_outward_behavior_as_not_found(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    corrupt_supersedes(journal, personal.record_id, sensitive.record_id)
    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    assert not any(row.get("record_id") == sensitive.record_id for row in related.related)
    record = b.get_record(personal.record_id, allowed_workspaces=["personal"])
    assert record.supersedes is None
    with pytest.raises(BrainError) as err:
        b.get_record(sensitive.record_id, allowed_workspaces=["personal"], sensitive_allowed=False)
    assert err.value.code == "BRAIN_SENSITIVE_SCOPE_DENIED"
    # positive control: with an explicit sensitive grant the same target becomes visible
    related_granted = b.get_related(personal.record_id, allowed_workspaces=["personal"], sensitive_allowed=True, sensitive_workspaces=["personal"])
    assert any(row.get("record_id") == sensitive.record_id for row in related_granted.related)


def test_foreign_workspace_direct_fetch_is_not_found_not_denied(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    with pytest.raises(BrainError) as err:
        b.get_record(foreign.record_id, allowed_workspaces=["personal"])
    assert err.value.code == "BRAIN_RECORD_NOT_FOUND"


# ---------------------------------------------------------------------------
# Regression: valid same-workspace relations and supersedes must stay visible.
# ---------------------------------------------------------------------------

def test_valid_same_workspace_link_and_supersede_remain_visible(tmp_path):
    b, journal = brain(tmp_path)
    source = b.record_problem(statement="Root cause", impact="test", source_assertion="explicit_user_confirmation", workspace="personal")
    first = b.record_decision(statement="Decide", rationale="because", reason="valid", verdict="accepted",
                               evidence=["doc://x"], links=[{"target_record_id": source.record_id, "relation": "addresses"}],
                               source_assertion="explicit_user_confirmation", workspace="personal", idempotency_key="valid-first")
    second = b.record_decision(statement="Decide v2", rationale="because v2", reason="valid", verdict="accepted",
                                evidence=["doc://y"], supersedes=first.record_id, change_reason="clarified",
                                source_assertion="explicit_user_confirmation", workspace="personal")
    incoming = b.get_related(source.record_id, allowed_workspaces=["personal"]).related
    assert any(row.get("direction") == "incoming" and row.get("record_id") == first.record_id for row in incoming)
    superseding = b.get_related(second.record_id, allowed_workspaces=["personal"]).related
    assert any(row.get("relation") == "supersedes" and row.get("record_id") == first.record_id for row in superseding)
    record = b.get_record(second.record_id, allowed_workspaces=["personal"])
    assert record.supersedes == first.record_id


# ---------------------------------------------------------------------------
# I10 property: every id returned via any expansion path must be directly,
# independently fetchable by the same caller (spec S5.2, invariant I4/I10).
# ---------------------------------------------------------------------------

def collect_ids_from_search(result):
    ids = set()
    for item in result.results:
        ids.add(item.record_id)
        if item.provenance.supersedes: ids.add(item.provenance.supersedes)
        if item.provenance.superseded_by: ids.add(item.provenance.superseded_by)
        for link in item.artifact_links:
            if link.get("record_id"): ids.add(link["record_id"])
    return ids


def collect_ids_from_related(result):
    ids = set()
    for row in result.related:
        if row.get("record_id"): ids.add(row["record_id"])
    return ids


def test_i10_every_expanded_id_is_directly_fetchable_with_same_scope(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    plant_record(journal, "rec-hidden-status", "personal", status="rejected")
    # a legitimate same-workspace incoming link plus every corrupt shape at once
    b.record_decision(statement="Legit decision", rationale="because", reason="valid", verdict="accepted",
                       evidence=["doc://z"], links=[{"target_record_id": personal.record_id, "relation": "addresses"}],
                       source_assertion="explicit_user_confirmation", workspace="personal", idempotency_key="i10-legit")
    corrupt_supersedes(journal, personal.record_id, foreign.record_id)
    corrupt_link(journal, foreign.record_id, "record://" + personal.record_id, relation="touches")
    corrupt_link(journal, personal.record_id, "record://rec-does-not-exist")

    # A single consistent grant for this caller, reused for every expansion
    # path and for the direct-fetch replay below -- I10 compares apples to
    # apples: the same scope must explain every id the caller was shown.
    allowed = ["personal"]; sensitive_allowed = True; sensitive_ws = ["personal"]
    related = b.get_related(personal.record_id, allowed_workspaces=allowed, sensitive_allowed=sensitive_allowed, sensitive_workspaces=sensitive_ws)
    ids = collect_ids_from_related(related)

    retrieval = project(journal, tmp_path, name="i10-retrieval.db")
    reader = scoped_reader(journal, retrieval)
    result = reader.search(query="Scoped source", workspaces=allowed, types=["problem", "decision"], limit=10,
                            include_artifacts=True, sensitive_allowed=sensitive_allowed)
    ids |= collect_ids_from_search(result)

    forbidden = {foreign.record_id, "rec-does-not-exist", "rec-hidden-status"}
    assert not (ids & forbidden), f"scope leak: {ids & forbidden}"

    for record_id in ids:
        b.get_record(record_id, allowed_workspaces=allowed, sensitive_allowed=sensitive_allowed, sensitive_workspaces=sensitive_ws)  # must not raise


# ---------------------------------------------------------------------------
# UNRESTRICTED_OPERATOR_SCOPE: an explicit trusted-local sentinel, never MCP.
# ---------------------------------------------------------------------------

def test_unrestricted_operator_scope_bypasses_workspace_filter_for_trusted_callers(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    record = b.get_record(foreign.record_id, allowed_workspaces=UNRESTRICTED_OPERATOR_SCOPE)
    assert record.record_id == foreign.record_id
    corrupt_supersedes(journal, personal.record_id, foreign.record_id)
    related = b.get_related(personal.record_id, allowed_workspaces=UNRESTRICTED_OPERATOR_SCOPE)
    assert any(row.get("record_id") == foreign.record_id for row in related.related)


def test_mcp_server_source_never_references_unrestricted_operator_scope():
    source = (ROOT / "brain" / "mcp_server.py").read_text()
    assert "UNRESTRICTED_OPERATOR_SCOPE" not in source
    import brain.mcp_server as mcp_module
    assert not hasattr(mcp_module, "UNRESTRICTED_OPERATOR_SCOPE")
