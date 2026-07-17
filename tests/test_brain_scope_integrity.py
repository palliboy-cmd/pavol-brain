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
import ast
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
from brain.record_uri import classify_record_uri, record_target_id, CANONICAL_RECORD_TARGET

from test_brain_write import brain, problem, NoopTransport, FakeEmbedder

pytestmark = pytest.mark.acceptance  # §10 rows 11-14 -- see tests/ACCEPTANCE_MATRIX.md

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


def test_b4_probe_corrupt_superseded_by_nulled_in_search_provenance(tmp_path):
    # §10 row 12: the search path specifically, not just get_related/get_record
    # (the sibling supersedes case is covered end-to-end via search in
    # test_b4_probe_include_artifacts_and_provenance_sanitized_in_search).
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    corrupt_superseded_by(journal, personal.record_id, foreign.record_id)
    retrieval = project(journal, tmp_path)
    reader = scoped_reader(journal, retrieval)
    result = reader.search(query="Scoped source", workspaces=["personal"], types=["problem"], limit=10,
                            sensitive_allowed=True)
    row = next(item for item in result.results if item.record_id == personal.record_id)
    assert row.provenance.superseded_by is None
    payload = json.dumps(result.model_dump(mode="json"))
    assert foreign.record_id not in payload


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
# F1 (package-5-scope-safe-retrieval-review.md): a corrupt artifact_links row
# whose URI merely *looks* like a record scheme -- wrong case, extra/missing
# slashes, surrounding whitespace, query/fragment, or percent-encoded -- must
# never resolve as a record relation and must never fall through to be
# treated as an ordinary artifact URI either, since its raw text still names
# a foreign record. The row is dropped outright: no URI, no id, anywhere in
# the response.
# ---------------------------------------------------------------------------

MALFORMED_RECORD_URI_TEMPLATES = [
    "Record://{id}",       # case-variant scheme
    "RECORD://{id}",       # case-variant scheme
    "record:/{id}",        # single slash
    "record:////{id}",     # extra slashes
    " record://{id}",      # leading whitespace
    "record://{id} ",      # trailing whitespace
    "record://",           # empty id
    "record://{id}?x=1",   # query suffix
    "record://{id}#fragment",  # fragment suffix
    "record%3A%2F%2F{id}",  # percent-encoded scheme+slashes
]


@pytest.mark.parametrize("uri_template", MALFORMED_RECORD_URI_TEMPLATES)
def test_f1_malformed_record_like_uri_dropped_from_get_related_and_search(tmp_path, uri_template):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    uri = uri_template.format(id=foreign.record_id)
    corrupt_link(journal, personal.record_id, uri)
    journal_before = journal.read_bytes()

    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    related_payload = json.dumps(related.model_dump(mode="json"))
    assert foreign.record_id not in related_payload
    assert uri not in related_payload
    # the malformed row is dropped outright, not merely stripped of its id --
    # only the pre-existing legitimate evidence link (from problem()) remains.
    assert len(related.related) == 1
    entry = related.related[0]
    assert entry["relation"] == "evidence"
    assert entry["artifact_uri"] == "doc://m1/problem"
    assert entry["record_id"] == personal.record_id
    # doc:// is not a deterministically verifiable scheme (B9/Package 6):
    # the trust view fails safe to unverified_reference, never verified.
    trust = entry["artifact_trust"]
    assert trust["state"] == "unverified_reference"
    assert trust["method"] == "not_deterministically_verifiable"
    assert trust["verifier"] == "server-artifact-validator"
    assert trust["verified_at"]
    assert trust["digest"] is None

    retrieval = project(journal, tmp_path, name=f"f1-{abs(hash(uri))}.db")
    reader = scoped_reader(journal, retrieval)
    result = reader.search(query="Scoped source", workspaces=["personal"], types=["problem"], limit=10,
                            include_artifacts=True, sensitive_allowed=True)
    search_payload = json.dumps(result.model_dump(mode="json"))
    assert foreign.record_id not in search_payload
    assert uri not in search_payload

    # sanitization must never touch canonical journal state
    assert journal.read_bytes() == journal_before

    # no error path is exercised by a malformed row (it's silently dropped),
    # but confirm that directly, too: no exception, no error-detail leak.
    try:
        b.get_related(personal.record_id, allowed_workspaces=["personal"])
    except BrainError as exc:
        assert foreign.record_id not in json.dumps(exc.details)


def test_canonical_record_relation_resolves_within_scope(tmp_path):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    other_personal = b.record_problem(**problem(statement="Other personal target", workspace="personal",
                                                  idempotency_key="scope-other-personal"))
    corrupt_link(journal, personal.record_id, "record://" + other_personal.record_id)
    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    assert any(row.get("record_id") == other_personal.record_id for row in related.related)


def test_canonical_record_relation_to_foreign_workspace_still_filtered(tmp_path):
    # same canonical shape as the positive case above, pointed cross-workspace
    # -- the pre-existing B4 filter (not F1) must still reject it.
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    corrupt_link(journal, personal.record_id, "record://" + foreign.record_id)
    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    assert not any(row.get("record_id") == foreign.record_id for row in related.related)


@pytest.mark.parametrize("scheme", ["repo", "git", "doc", "adr", "route", "workspace"])
def test_non_record_schemes_remain_normal_artifacts_unaffected(tmp_path, scheme):
    b, journal, personal, foreign, sensitive = seeded(tmp_path)
    uri = f"{scheme}://something"
    corrupt_link(journal, personal.record_id, uri, relation="touches")
    related = b.get_related(personal.record_id, allowed_workspaces=["personal"])
    assert any(row.get("artifact_uri") == uri and row.get("relation") == "touches" for row in related.related)
    assert classify_record_uri(uri) == "normal_artifact"


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

def collect_uri_derived_ids(rows):
    """Scan artifact_uri text directly (independent of any pre-populated
    record_id field) for canonical record relations. This is the collector
    half of F1's fix: it must find exactly the same ids the record_id field
    already carries for canonical rows, and nothing at all for malformed
    record-like rows, since those must be dropped outright rather than
    surfaced with a URI string a caller could parse themselves."""
    ids = set()
    for row in rows:
        uri = row.get("artifact_uri")
        if uri and classify_record_uri(uri) == CANONICAL_RECORD_TARGET:
            ids.add(record_target_id(uri))
    return ids


def collect_ids_from_search(result):
    ids = set()
    for item in result.results:
        ids.add(item.record_id)
        if item.provenance.supersedes: ids.add(item.provenance.supersedes)
        if item.provenance.superseded_by: ids.add(item.provenance.superseded_by)
        for link in item.artifact_links:
            if link.get("record_id"): ids.add(link["record_id"])
        ids |= collect_uri_derived_ids(item.artifact_links)
    return ids


def collect_ids_from_related(result):
    ids = set()
    for row in result.related:
        if row.get("record_id"): ids.add(row["record_id"])
    ids |= collect_uri_derived_ids(result.related)
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
    # F1: scheme-variant / malformed record-like URIs embedding the same
    # corrupt ids, planted alongside the canonical corrupt shapes above --
    # I10 must catch leaks through raw artifact_uri text, not just through
    # structured record_id fields (that's exactly where F1 leaked).
    corrupt_link(journal, personal.record_id, "Record://" + foreign.record_id, relation="touches")
    corrupt_link(journal, personal.record_id, "record:/rec-does-not-exist", relation="addresses")

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

    # Raw substring scan over the whole serialized response: a malformed
    # record-like URI is never classified/extracted, so it would never show
    # up in the structured `ids` set above even if it leaked -- it must be
    # entirely absent from the response text instead.
    payload_text = json.dumps(related.model_dump(mode="json")) + json.dumps(result.model_dump(mode="json"))
    for fid in forbidden:
        assert fid not in payload_text, f"raw id leak in serialized response: {fid}"

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


def _brain_module_path(module_name, package="brain"):
    rel = module_name[len(package) + 1:] if module_name != package else "__init__"
    return ROOT / package / (rel.replace(".", "/") + ".py")


def _direct_brain_imports(module_path, package="brain"):
    """First-party brain.* module names this file imports directly (relative
    or absolute)."""
    tree = ast.parse(module_path.read_text())
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level and node.level >= 1:  # relative: `.config`, `. ` (bare package)
                if node.module: imports.add(f"{package}.{node.module}")
                else:
                    for alias in node.names: imports.add(f"{package}.{alias.name}")
            elif node.module == package:
                for alias in node.names: imports.add(f"{package}.{alias.name}")
            elif node.module and node.module.startswith(package + "."):
                imports.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == package or alias.name.startswith(package + "."):
                    imports.add(alias.name)
    return imports


def _transitive_brain_imports(entry_module, package="brain", exclude=frozenset()):
    """Every brain.* module reachable from entry_module by direct or
    transitive import, excluding `exclude` (and anything only reachable
    through it)."""
    seen, stack = set(), [entry_module]
    while stack:
        name = stack.pop()
        if name in seen or name in exclude: continue
        seen.add(name)
        path = _brain_module_path(name, package)
        if not path.is_file(): continue
        stack.extend(_direct_brain_imports(path, package) - seen)
    return seen


def test_mcp_transitive_imports_never_reference_unrestricted_operator_scope():
    """F3 (package-5-scope-safe-retrieval-review.md): the guard above reads
    only brain/mcp_server.py's source text, so it would silently stop
    covering the sentinel if MCP tool bodies were ever moved into a helper
    module. This walks the real import graph -- every brain.* module
    mcp_server.py imports, directly or transitively, except brain.api itself
    (where the sentinel is defined and legitimately named) -- and asserts
    none of them contains a Name/Attribute reference to
    UNRESTRICTED_OPERATOR_SCOPE or a `from brain.api import *` that could
    reintroduce it anonymously."""
    modules = _transitive_brain_imports("brain.mcp_server", exclude={"brain.api"})
    modules.add("brain.mcp_server")
    offenders = []
    for name in sorted(modules):
        path = _brain_module_path(name)
        if not path.is_file(): continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "UNRESTRICTED_OPERATOR_SCOPE":
                offenders.append((name, node.lineno, "Name"))
            elif isinstance(node, ast.Attribute) and node.attr == "UNRESTRICTED_OPERATOR_SCOPE":
                offenders.append((name, node.lineno, "Attribute"))
            elif isinstance(node, ast.ImportFrom) and node.module == "brain.api" and any(a.name == "*" for a in node.names):
                offenders.append((name, node.lineno, "star-import of brain.api"))
            elif isinstance(node, ast.ImportFrom) and any(a.name == "UNRESTRICTED_OPERATOR_SCOPE" for a in node.names):
                # catches `from brain.api import UNRESTRICTED_OPERATOR_SCOPE as X`,
                # which the Name check above can't see once it's rebound to X
                offenders.append((name, node.lineno, "aliased import of UNRESTRICTED_OPERATOR_SCOPE"))
    assert not offenders, f"UNRESTRICTED_OPERATOR_SCOPE reachable from mcp_server.py via: {offenders}"
