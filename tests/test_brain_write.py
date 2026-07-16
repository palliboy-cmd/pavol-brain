import hashlib
import json
import sqlite3
import sys
import traceback
from pathlib import Path

import pytest
import subprocess

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"tests"));sys.path.insert(0,str(ROOT/"spike"))

from brain.api import Brain
from brain.config import BrainConfig
from brain.errors import BrainError
from brain.migrations import inspect_m1,migrate_m1
from brain.projector import ProjectorConfig,ProjectionProjector
from brain.projector.models import ProjectionStatus
from brain.write_policy import collect_client_strings
from brain.write_envelope import FIELD_CLASSIFICATION,REQUEST_MODELS,CLASSIFICATIONS,OUT_OF_BAND_FIELDS
from journal_fixture import journal_fixture
from src.journal import fold

# Stable fake canaries matching SECRET_PATTERNS (brain/write_policy.py). CANARY
# also violates the tight verification-key / request_id charset (it contains
# "="), so the same value exercises both the shape gates and the Band C
# content scan. KEY_SAFE_CANARY is alnum/dash only: it passes the
# verification-key shape gate so a positive result proves Band C's dict-key
# scan (B6), not the shape constraint, caught it.
CANARY = "api_key=sk-live-fakeFAKE1234567890fake"
KEY_SAFE_CANARY = "sk-live-fakeFAKE1234567890fake"

class NoopTransport:
    def embed(self,text):return [1.0,0.0,0.0,0.0]

class FakeEmbedder:
    def embed_document(self,text):
        seed=int(hashlib.sha256(text.encode()).hexdigest()[:8],16)
        return [float((seed>>(i*4))%11+1) for i in range(4)],"fake"

def brain(tmp_path,identity="writer",instance="personal"):
    journal=tmp_path/"journal.db";journal_fixture(journal,instance_id=instance)
    config=BrainConfig(journal_db_path=journal,retrieval_db_path=tmp_path/"retrieval.db",embedding_dimension=4,
                       endpoint_probe_timeout=.01,client_identity=identity,instance_id=instance)
    return Brain(config,NoopTransport()),journal

def brain_with_audit(tmp_path,identity="writer",instance="personal"):
    journal=tmp_path/"journal.db";journal_fixture(journal,instance_id=instance)
    audit_log=tmp_path/"audit.jsonl"
    config=BrainConfig(journal_db_path=journal,retrieval_db_path=tmp_path/"retrieval.db",embedding_dimension=4,
                       endpoint_probe_timeout=.01,client_identity=identity,instance_id=instance,audit_log_path=audit_log)
    return Brain(config,NoopTransport()),journal,audit_log

def attached_brain(journal,tmp_path,identity,instance="personal"):
    return Brain(BrainConfig(journal_db_path=journal,retrieval_db_path=tmp_path/f"{identity}.retrieval.db",embedding_dimension=4,
                             endpoint_probe_timeout=.01,client_identity=identity,instance_id=instance),NoopTransport())

def outcome(**overrides):
    return {"summary":"Implemented M1 write path","changes":["added writer"],"verification":{"tests":"pass"},
            "artifacts":["repo://pavol-brain/brain/api.py"],"source_assertion":"verified_tool_result",
            "workspace":"personal",**overrides}

def decision(**overrides):
    return {"statement":"Use approach X","rationale":"Because Y","reason":"validated","verdict":"accepted",
            "evidence":["doc://m1/decision"],"source_assertion":"explicit_user_confirmation","workspace":"personal",**overrides}

def problem(**overrides):
    return {"statement":"Some problem","impact":"Some impact","evidence":["doc://m1/problem"],
            "source_assertion":"explicit_user_confirmation","workspace":"personal",**overrides}

def uri_canary(canary=KEY_SAFE_CANARY):
    return f"doc://artifact/{canary}"

def audit_bytes(audit_log):
    return audit_log.read_bytes() if audit_log.exists() else b""

def row_counts(journal):
    con=sqlite3.connect(journal)
    counts=tuple(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                 for t in ("memory_records","memory_events","record_state","artifact_links"))
    con.close();return counts

def created_event(journal,record_id):
    con=sqlite3.connect(journal)
    row=con.execute("SELECT event_id,data FROM memory_events WHERE record_id=? AND event_type='record_created'",(record_id,)).fetchone()
    con.close();return row

def test_policy_bands_secret_filter_idempotency_and_provenance(tmp_path):
    b,journal=brain(tmp_path)
    accepted=b.record_outcome(**outcome(idempotency_key="same"));again=b.record_outcome(**outcome(idempotency_key="same"))
    assert accepted.status=="accepted" and accepted.policy_band=="A" and not accepted.idempotent
    assert again.record_id==accepted.record_id and again.idempotent
    candidate=b.record_outcome(**outcome(artifacts=[],source_assertion="agent_inference",idempotency_key="candidate"))
    assert candidate.status=="candidate" and candidate.policy_band=="B"
    with pytest.raises(BrainError,match="BRAIN_IDEMPOTENCY_CONFLICT"):
        b.record_outcome(**outcome(summary="different",idempotency_key="same"))
    with pytest.raises(BrainError,match="BRAIN_WRITE_SECRET_REJECTED"):
        b.record_outcome(**outcome(summary="password = super-secret-value",idempotency_key="secret"))
    con=sqlite3.connect(journal);con.row_factory=sqlite3.Row
    row=con.execute("SELECT agent_id,session_ref,source_assertion,schema_version FROM memory_records WHERE record_id=?",(accepted.record_id,)).fetchone()
    assert dict(row)=={"agent_id":"writer","session_ref":None,"source_assertion":"verified_tool_result","schema_version":2}
    raw=json.loads(con.execute("SELECT raw_input FROM memory_records WHERE record_id=?",(accepted.record_id,)).fetchone()[0])
    assert raw["record_type"]=="outcome" and raw["metadata"]["source_assertion"]=="verified_tool_result"

def test_idempotency_is_agent_namespaced_and_semantic_duplicates_are_candidates(tmp_path):
    agent_a,journal=brain(tmp_path,"agent-a");agent_b=attached_brain(journal,tmp_path,"agent-b")
    first=agent_a.record_outcome(**outcome(idempotency_key="shared-key"))
    retry=agent_a.record_outcome(**outcome(idempotency_key="shared-key"))
    assert retry.idempotent and retry.record_id==first.record_id
    with pytest.raises(BrainError,match="BRAIN_IDEMPOTENCY_CONFLICT"):
        agent_a.record_outcome(**outcome(summary="different request",idempotency_key="shared-key"))
    other=agent_b.record_outcome(**outcome(idempotency_key="shared-key"))
    assert not other.idempotent and other.record_id!=first.record_id and other.status=="candidate"
    automatic=agent_b.record_outcome(**outcome(idempotency_key=None))
    assert automatic.status=="candidate" and automatic.record_id not in {first.record_id,other.record_id}
    con=sqlite3.connect(journal)
    event=json.loads(con.execute("SELECT data FROM memory_events WHERE record_id=? AND event_type='record_created'",(other.record_id,)).fetchone()[0])
    assert event["possible_duplicate_of"]==first.record_id

def test_idempotent_replay_returns_original_record_and_writes_no_new_rows(tmp_path):
    b,journal=brain(tmp_path)
    first=b.record_outcome(**outcome(idempotency_key="replay-key"))
    before=row_counts(journal)
    again=b.record_outcome(**outcome(idempotency_key="replay-key"))
    assert again.idempotent and again.record_id==first.record_id and again.event_id==first.event_id
    assert row_counts(journal)==before

def test_idempotency_explicit_key_metadata_conflict_matrix(tmp_path):
    # §10 row 17b: same explicit key, same payload, metadata alone diverges.
    b,journal=brain(tmp_path)
    target=b.record_outcome(**outcome(idempotency_key="meta-conflict-target"))
    linkable=b.record_problem(statement="Linkable problem",impact="referenced by the link-metadata conflict case",
                              evidence=["doc://m1/linkable"],source_assertion="explicit_user_confirmation",workspace="personal")
    first=b.record_outcome(**outcome(idempotency_key="meta-conflict-key"))
    before=row_counts(journal)
    original_event=created_event(journal,first.record_id)
    variants=[
        {"session_ref":"different-session"},
        {"source_ref":"different-source"},
        {"valid_at":"2026-07-11T00:00:00+00:00"},
        {"links":[{"target_record_id":linkable.record_id,"relation":"addresses"}]},
        {"supersedes":target.record_id,"change_reason":"pin metadata conflict"},
    ]
    for variant in variants:
        with pytest.raises(BrainError,match="BRAIN_IDEMPOTENCY_CONFLICT"):
            b.record_outcome(**outcome(idempotency_key="meta-conflict-key",**variant))
        assert row_counts(journal)==before
    assert created_event(journal,first.record_id)==original_event

def test_idempotency_legacy_row_without_request_hash_forces_conflict(tmp_path):
    # B8 probe: a stored record_created event missing request_hash must never
    # be treated as a safe idempotent replay, even for the original payload.
    b,journal=brain(tmp_path)
    original=b.record_outcome(**outcome(idempotency_key="legacy-probe-key"))
    con=sqlite3.connect(journal)
    event_id,data=con.execute("SELECT event_id,data FROM memory_events WHERE record_id=? AND event_type='record_created'",(original.record_id,)).fetchone()
    corrupted=json.loads(data);del corrupted["request_hash"]
    con.execute("UPDATE memory_events SET data=? WHERE event_id=?",(json.dumps(corrupted),event_id))
    con.commit();con.close()
    before=row_counts(journal)
    with pytest.raises(BrainError) as excinfo:
        b.record_outcome(**outcome(idempotency_key="legacy-probe-key",session_ref="different-session"))
    assert excinfo.value.code=="BRAIN_IDEMPOTENCY_CONFLICT"
    assert excinfo.value.details.get("reason")=="legacy_record_without_request_hash"
    assert row_counts(journal)==before
    con=sqlite3.connect(journal)
    stored=json.loads(con.execute("SELECT data FROM memory_events WHERE event_id=?",(event_id,)).fetchone()[0])
    con.close()
    assert stored==corrupted

def test_idempotency_explicit_key_across_workspace_and_type_conflicts(tmp_path):
    # §10 row 17d: an explicit key names one logical write; reuse across
    # workspace or record type is a conflict, never a fork.
    b,journal=brain(tmp_path)
    b.record_outcome(**outcome(idempotency_key="cross-scope-key",workspace="personal"))
    before=row_counts(journal)
    with pytest.raises(BrainError,match="BRAIN_IDEMPOTENCY_CONFLICT"):
        b.record_outcome(**outcome(idempotency_key="cross-scope-key",workspace="ai-pos"))
    assert row_counts(journal)==before
    with pytest.raises(BrainError,match="BRAIN_IDEMPOTENCY_CONFLICT"):
        b.record_problem(statement="Different type reusing the same explicit key",impact="pin cross-type key reuse",
                         evidence=["doc://m1/cross-type"],source_assertion="explicit_user_confirmation",
                         workspace="personal",idempotency_key="cross-scope-key")
    assert row_counts(journal)==before

def test_idempotency_no_explicit_key_cross_workspace_produces_independent_records(tmp_path):
    # §10 row 18: same content, different workspaces, no explicit key.
    b,journal=brain(tmp_path)
    before=row_counts(journal)
    personal=b.record_outcome(**outcome(workspace="personal"))
    other=b.record_outcome(**outcome(workspace="ai-pos"))
    assert personal.record_id!=other.record_id
    assert personal.status=="accepted" and other.status=="accepted"
    assert row_counts(journal)==tuple(x+2 for x in before)

def test_idempotency_supersede_replay_supersedes_target_exactly_once(tmp_path):
    # §10 row 19: identical supersede replay returns the original result;
    # the target is superseded exactly once, never a second supersede event.
    b,journal=brain(tmp_path)
    target=b.record_outcome(**outcome(idempotency_key="supersede-replay-target"))
    kwargs=outcome(summary="Superseding outcome",idempotency_key="supersede-replay-key",
                   supersedes=target.record_id,change_reason="pin supersede replay")
    first=b.record_outcome(**kwargs)
    before=row_counts(journal)
    again=b.record_outcome(**kwargs)
    assert again.idempotent and again.record_id==first.record_id
    assert row_counts(journal)==before
    con=sqlite3.connect(journal)
    assert con.execute("SELECT count(*) FROM memory_events WHERE record_id=? AND event_type='record_superseded'",(target.record_id,)).fetchone()[0]==1
    assert tuple(con.execute("SELECT status,superseded_by FROM record_state WHERE record_id=?",(target.record_id,)).fetchone())==("superseded",first.record_id)

def test_instance_namespace_and_library_mapping_are_enforced(tmp_path):
    personal_dir=tmp_path/"personal-instance";work_dir=tmp_path/"work-instance";personal_dir.mkdir();work_dir.mkdir()
    personal,pjournal=brain(personal_dir,"same-agent")
    _,wjournal=brain(work_dir,"seed",instance="work");work=attached_brain(wjournal,work_dir,"same-agent","work")
    p=personal.record_outcome(**outcome(summary="same semantic handoff",workspace="personal",idempotency_key="same-key"))
    w=work.record_outcome(**outcome(summary="same semantic handoff",workspace="sap-work",idempotency_key="same-key"))
    assert p.status==w.status=="accepted" and p.record_id!=w.record_id
    assert sqlite3.connect(wjournal).execute("SELECT sensitivity FROM memory_records WHERE record_id=?",(w.record_id,)).fetchone()[0]=="sensitive"
    with pytest.raises(BrainError,match="BRAIN_INSTANCE_DENIED"):
        work.record_outcome(**outcome(workspace="personal",idempotency_key="wrong-instance"))
    legacy=attached_brain(pjournal,personal_dir,"legacy-agent","legacy")
    with pytest.raises(BrainError,match="BRAIN_WRITE_DISABLED"):
        legacy.record_outcome(**outcome(idempotency_key="legacy-write"))

def test_artifact_validation_controls_band_a_and_writes_audit_events(tmp_path):
    b,journal=brain(tmp_path)
    valid=b.record_outcome(**outcome(idempotency_key="valid-artifact"))
    missing=b.record_outcome(**outcome(artifacts=["repo://pavol-brain/does-not-exist"],idempotency_key="missing-artifact"))
    document=b.record_outcome(**outcome(artifacts=["doc://synthetic/unverified"],idempotency_key="doc-artifact"))
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    commit=b.record_outcome(**outcome(artifacts=[],commit=f"git://pavol-brain/commit/{head}",idempotency_key="commit-artifact"))
    assert valid.status==commit.status=="accepted"
    assert missing.status==document.status=="candidate"
    con=sqlite3.connect(journal);con.row_factory=sqlite3.Row
    origins={row["record_id"]:row["origin"] for row in con.execute("SELECT record_id,origin FROM artifact_links WHERE record_id IN (?,?,?,?)",(valid.record_id,missing.record_id,document.record_id,commit.record_id))}
    assert origins[valid.record_id]==origins[commit.record_id]=="deterministic"
    assert origins[missing.record_id]==origins[document.record_id]=="derived"
    states={row["artifact_record_id"]:row["state"] for row in con.execute("SELECT artifact_record_id,state FROM artifact_validation_events WHERE artifact_record_id IN (?,?,?,?)",(valid.record_id,missing.record_id,document.record_id,commit.record_id))}
    assert states=={valid.record_id:"verified_active",missing.record_id:"verified_inactive",document.record_id:"unknown",commit.record_id:"verified_active"}

def test_band_c_filters_all_persisted_client_text(tmp_path):
    b,_=brain(tmp_path)
    with pytest.raises(BrainError,match="BRAIN_WRITE_SECRET_REJECTED"):
        b.record_outcome(**outcome(idempotency_key="api_key=super-secret-value"))
    first=b.record_outcome(**outcome(idempotency_key="supersede-source"))
    with pytest.raises(BrainError,match="BRAIN_WRITE_CONTENT_REJECTED"):
        b.record_outcome(**outcome(summary="replacement",supersedes=first.record_id,
            change_reason="User: copy this transcript\nAssistant: hidden",idempotency_key="transcript-reason"))
    with pytest.raises(BrainError,match="BRAIN_WRITE_CONTENT_REJECTED"):
        b.record_outcome(**outcome(idempotency_key="chain-of-thought-in-key"))
    with pytest.raises(BrainError,match="BRAIN_WRITE_SECRET_REJECTED"):
        b.record_outcome(**outcome(artifacts=["doc://artifact/abcdefghijklmnopqrstuvwxyzABCDEFG123456"],idempotency_key="secret-artifact"))

def test_decision_payload_record_links_and_supersede_are_append_only(tmp_path):
    b,journal=brain(tmp_path)
    problem=b.record_problem(statement="Agents lose context",impact="Repeated explanation",evidence=["doc://m1/problem"],
                             source_assertion="explicit_user_confirmation",workspace="personal")
    first=b.record_decision(statement="Use separate Brain instances",rationale="Isolation by construction",alternatives=[{
        "option":"one row-filtered instance","verdict":"rejected","reason":"larger leak surface","reopen_when":"cross-instance retrieval becomes required","evidence":["doc://m1/isolation"]}],
        verdict="accepted",reason="zero leak",reopen_when=None,evidence=["doc://m1/decision"],
        links=[{"target_record_id":problem.record_id,"relation":"addresses"}],source_assertion="explicit_user_confirmation",workspace="personal",idempotency_key="first-decision")
    conflict=b.record_decision(statement="Use separate Brain instances",rationale="Conflicting rationale",alternatives=[],verdict="rejected",
        reason="conflict",reopen_when="later",evidence=["doc://m1/conflict"],source_assertion="explicit_user_confirmation",workspace="personal")
    assert conflict.status=="candidate" and conflict.policy_band=="B"
    second=b.record_decision(statement="Use separate Brain instances v2",rationale="Same invariant",alternatives=[],verdict="accepted",
        reason="clarified deployment",reopen_when=None,evidence=["doc://m1/decision-v2"],supersedes=first.record_id,
        change_reason="deployment clarified",source_assertion="explicit_user_confirmation",workspace="personal")
    con=sqlite3.connect(journal);con.row_factory=sqlite3.Row
    payload=json.loads(con.execute("SELECT payload FROM memory_records WHERE record_id=?",(first.record_id,)).fetchone()[0])
    assert set(payload)=={"statement","rationale","alternatives","verdict","reason","reopen_when","evidence"}
    assert con.execute("SELECT count(*) FROM memory_records WHERE record_id IN (?,?)",(first.record_id,second.record_id)).fetchone()[0]==2
    old=con.execute("SELECT status,superseded_by,change_reason FROM record_state WHERE record_id=?",(first.record_id,)).fetchone()
    assert tuple(old)==("superseded",second.record_id,"deployment clarified")
    link=con.execute("SELECT artifact_uri,relation FROM artifact_links WHERE record_id=? AND artifact_uri LIKE 'record://%'",(first.record_id,)).fetchone()
    assert tuple(link)==("record://"+problem.record_id,"addresses")
    assert con.execute("SELECT count(*) FROM memory_events WHERE record_id=? AND event_type='record_superseded'",(first.record_id,)).fetchone()[0]==1
    for record_id in (first.record_id,second.record_id):
        expected=fold(con.execute("SELECT * FROM memory_events WHERE record_id=? ORDER BY occurred_at,event_id",(record_id,)).fetchall())
        stored=dict(con.execute("SELECT * FROM record_state WHERE record_id=?",(record_id,)).fetchone())
        assert all(stored[key]==value for key,value in expected.items())
    repeated=b.record_decision(statement="Use separate Brain instances",rationale="Isolation by construction",alternatives=[{
        "option":"one row-filtered instance","verdict":"rejected","reason":"larger leak surface","reopen_when":"cross-instance retrieval becomes required","evidence":["doc://m1/isolation"]}],
        verdict="accepted",reason="zero leak",reopen_when=None,evidence=["doc://m1/decision"],
        links=[{"target_record_id":problem.record_id,"relation":"addresses"}],source_assertion="explicit_user_confirmation",workspace="personal",idempotency_key="first-decision")
    assert repeated.idempotent and repeated.record_id==first.record_id and repeated.status=="accepted"
    incoming=b.get_related(problem.record_id,allowed_workspaces=["personal"]).related
    assert any(row.get("direction")=="incoming" and row["record_id"]==first.record_id for row in incoming)

def test_problem_analysis_project_and_old_baseline_hashes_stay_stable(tmp_path):
    b,journal=brain(tmp_path)
    problem=b.record_problem(statement="Missing memory loop",impact="No agent handoff",evidence=["doc://m1/problem"],source_assertion="explicit_user_confirmation",workspace="personal")
    analysis=b.record_analysis(summary="Write path is missing",findings=["MCP is read-only"],evidence=["repo://pavol-brain/brain/mcp_server.py"],
                               links=[{"target_record_id":problem.record_id,"relation":"analyzes"}],source_assertion="authoritative_document",
                               source_ref="repo://pavol-brain/brain/mcp_server.py",workspace="personal")
    head=subprocess.run(["git","rev-parse","HEAD"],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    projected_outcome=b.record_outcome(summary="Projection fields",open_questions=["What follows?"],commit=f"git://pavol-brain/commit/{head}",
        source_assertion="explicit_user_confirmation",workspace="personal",idempotency_key="projection-outcome")
    retrieval=tmp_path/"retrieval.db";projector=ProjectionProjector(ProjectorConfig(journal,retrieval,"fake",4,"fake"),FakeEmbedder())
    while projector.run_once(100).status==ProjectionStatus.HEALTHY:pass
    con=sqlite3.connect(retrieval)
    assert {row[0] for row in con.execute("SELECT type FROM retrieval_documents WHERE record_id IN (?,?)",(problem.record_id,analysis.record_id))}=={"problem","analysis"}
    canonical=con.execute("SELECT canonical_text,artifacts_text FROM retrieval_documents WHERE record_id=?",(projected_outcome.record_id,)).fetchone()
    assert "What follows?" in canonical[0] and head in canonical[0] and head in canonical[1]
    baseline=sqlite3.connect(ROOT/"sqlite-spike/retrieval.db")
    for record_id,projection_hash in baseline.execute("SELECT record_id,projection_hash FROM retrieval_documents"):
        assert con.execute("SELECT projection_hash FROM retrieval_documents WHERE record_id=?",(record_id,)).fetchone()[0]==projection_hash

def test_search_filters_corrupt_cross_workspace_related_record_ids(tmp_path):
    b,journal=brain(tmp_path)
    personal=b.record_problem(statement="Scoped source",impact="test",source_assertion="explicit_user_confirmation",workspace="personal")
    foreign=b.record_problem(statement="Foreign target",impact="test",source_assertion="explicit_user_confirmation",workspace="ai-pos")
    created="2026-07-13T00:00:00+00:00";con=sqlite3.connect(journal)
    con.execute("INSERT INTO artifact_links VALUES (?,?,?,?,?,?,?)",(personal.record_id,"record://"+foreign.record_id,"addresses",1.0,"corrupt-fixture",created,1));con.commit();con.close()
    retrieval=tmp_path/"retrieval-scope.db";projector=ProjectionProjector(ProjectorConfig(journal,retrieval,"fake",4,"fake",instance_id="personal"),FakeEmbedder())
    while projector.run_once(100).status==ProjectionStatus.HEALTHY:pass
    scoped=Brain(BrainConfig(journal_db_path=journal,retrieval_db_path=retrieval,embedding_dimension=4,
                             endpoint_probe_timeout=.01,client_identity="reader",instance_id="personal"),NoopTransport())
    result=scoped.search(query="Scoped source",workspaces=["personal"],types=["problem"],limit=10,include_artifacts=True)
    row=next(item for item in result.results if item.record_id==personal.record_id)
    assert not any(link.get("record_id")==foreign.record_id for link in row.artifact_links)

def test_record_uri_is_rejected_in_evidence_artifacts_commit_and_alternatives_evidence(tmp_path):
    b, journal = brain(tmp_path)
    same_ws = b.record_problem(statement="Existing same-workspace record", impact="test",
                                source_assertion="explicit_user_confirmation", workspace="personal")
    foreign_ws = b.record_problem(statement="Existing foreign-workspace record", impact="test",
                                   source_assertion="explicit_user_confirmation", workspace="ai-pos")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()

    targets = {
        "dangling": "record://rec-does-not-exist",
        "same_workspace": "record://" + same_ws.record_id,
        "foreign_workspace": "record://" + foreign_ws.record_id,
    }

    def counts(con):
        return tuple(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("memory_records", "memory_events", "record_state", "artifact_links"))

    for case, uri in targets.items():
        con = sqlite3.connect(journal)
        before = counts(con)
        con.close()

        with pytest.raises(BrainError, match="BRAIN_INVALID_ARTIFACT_URI") as exc:
            b.record_problem(statement=f"evidence {case}", impact="test", evidence=[uri],
                              source_assertion="explicit_user_confirmation", workspace="personal",
                              idempotency_key=f"evidence-{case}")
        assert exc.value.details["values"] == [uri]

        with pytest.raises(BrainError, match="BRAIN_INVALID_ARTIFACT_URI"):
            b.record_outcome(**outcome(artifacts=[uri], idempotency_key=f"artifacts-{case}"))

        with pytest.raises(BrainError, match="BRAIN_INVALID_ARTIFACT_URI"):
            b.record_outcome(**outcome(artifacts=[], commit=uri, idempotency_key=f"commit-{case}"))

        with pytest.raises(BrainError, match="BRAIN_INVALID_ARTIFACT_URI"):
            b.record_decision(statement="A decision", rationale="Because", verdict="accepted", reason="test",
                               alternatives=[{"option": "alt", "verdict": "rejected", "reason": "no",
                                              "evidence": [uri]}],
                               source_assertion="explicit_user_confirmation", workspace="personal",
                               idempotency_key=f"alt-evidence-{case}")

        con = sqlite3.connect(journal)
        after = counts(con)
        con.close()
        assert after == before, f"partial write leaked for case={case}"

def test_record_scheme_removed_from_uri_policy_does_not_affect_typed_links(tmp_path):
    from brain.write_policy import URI_RE
    assert not URI_RE.fullmatch("record://rec_anything")
    assert URI_RE.fullmatch("doc://x") and URI_RE.fullmatch("repo://x") and URI_RE.fullmatch("git://x")
    b, journal = brain(tmp_path)
    problem = b.record_problem(statement="Agents lose context", impact="Repeated explanation",
                                evidence=["doc://m1/problem"], source_assertion="explicit_user_confirmation",
                                workspace="personal")
    decision = b.record_decision(statement="Use separate Brain instances", rationale="Isolation by construction",
                                  verdict="accepted", reason="zero leak", evidence=["doc://m1/decision"],
                                  links=[{"target_record_id": problem.record_id, "relation": "addresses"}],
                                  source_assertion="explicit_user_confirmation", workspace="personal")
    assert decision.status == "accepted"
    con = sqlite3.connect(journal); con.row_factory = sqlite3.Row
    link = con.execute("SELECT artifact_uri,relation FROM artifact_links WHERE record_id=? AND artifact_uri LIKE 'record://%'",
                        (decision.record_id,)).fetchone()
    assert tuple(link) == ("record://" + problem.record_id, "addresses")
    incoming = b.get_related(problem.record_id,allowed_workspaces=["personal"]).related
    assert any(row.get("direction") == "incoming" and row["record_id"] == decision.record_id for row in incoming)
    with pytest.raises(BrainError, match="BRAIN_LINK_TARGET_NOT_FOUND"):
        b.record_decision(statement="Dangling link", rationale="test", verdict="accepted", reason="test",
                           links=[{"target_record_id": "rec-does-not-exist", "relation": "addresses"}],
                           source_assertion="explicit_user_confirmation", workspace="personal")
    foreign = b.record_problem(statement="Foreign target", impact="test",
                                source_assertion="explicit_user_confirmation", workspace="ai-pos")
    with pytest.raises(BrainError, match="BRAIN_CROSS_WORKSPACE_LINK_DENIED"):
        b.record_decision(statement="Cross-workspace link", rationale="test", verdict="accepted", reason="test",
                           links=[{"target_record_id": foreign.record_id, "relation": "addresses"}],
                           source_assertion="explicit_user_confirmation", workspace="personal")

def test_b3_probe_rerun_record_uri_evidence_is_rejected(tmp_path):
    """Appendix A probe 1 re-run: baseline (pre-Package-2) accepted both of
    these into an accepted Band-A record and persisted artifact_links rows;
    both must now be rejected and nothing must be persisted."""
    b, journal = brain(tmp_path)
    foreign = b.record_problem(statement="Foreign-workspace record for the B3 probe", impact="test",
                                source_assertion="explicit_user_confirmation", workspace="ai-pos")

    def link_row_count():
        con = sqlite3.connect(journal)
        try:
            return con.execute("SELECT count(*) FROM artifact_links WHERE artifact_uri LIKE 'record://%'").fetchone()[0]
        finally:
            con.close()

    before = link_row_count()
    with pytest.raises(BrainError, match="BRAIN_INVALID_ARTIFACT_URI"):
        b.record_problem(statement="probe dangling", impact="test", evidence=["record://rec-does-not-exist"],
                          source_assertion="explicit_user_confirmation", workspace="personal")
    with pytest.raises(BrainError, match="BRAIN_INVALID_ARTIFACT_URI"):
        b.record_problem(statement="probe foreign workspace", impact="test",
                          evidence=["record://" + foreign.record_id],
                          source_assertion="explicit_user_confirmation", workspace="personal")
    assert link_row_count() == before

def test_m1_schema_migration_preserves_rows_and_requires_backup(tmp_path):
    old=tmp_path/"old.db";schema=(ROOT/"spike/schema/journal.sql").read_text()
    schema=schema.replace("'problem','analysis',","").replace("PRAGMA user_version=2;","PRAGMA user_version=1;")
    con=sqlite3.connect(old);con.executescript(schema)
    payload=json.dumps({"summary":"old","changes":[],"verification":{},"open_questions":[]})
    values=("old-rec",1,"outcome","personal","normal",payload,payload,"hash","old-key","legacy","imported_curated",None,None,None,1.0,"2026-01-01T00:00:00+00:00","2026-01-01T00:00:00+00:00")
    con.execute("INSERT INTO memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",values)
    con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)",("old-event","old-rec","record_created",values[-1],"legacy",'{"status":"accepted","review":"human_approved"}'))
    con.execute("INSERT INTO record_state VALUES (?,?,?,?,?,?,?,?,?,?,?)",("old-rec","accepted","human_approved",None,None,None,None,"none",None,None,"old-event"));con.commit();con.close()
    before=inspect_m1(old);report=migrate_m1(old,tmp_path/"old.backup.db");after=inspect_m1(old)
    assert report["changed"] and not before["already_m1"] and after["already_m1"]
    assert before["table_digests"]==after["table_digests"] and after["foreign_key_violations"]==[]

def test_representative_migration_preserves_all_canonical_tables_and_projection_hashes(tmp_path):
    journal=tmp_path/"representative-v1.db";journal_fixture(journal)
    downgrade=(ROOT/"spike/schema/m1_record_types.sql").read_text().replace("'problem','analysis',","").replace("PRAGMA user_version=2;","PRAGMA user_version=1;")
    con=sqlite3.connect(journal);con.execute("PRAGMA foreign_keys=OFF");con.execute("DROP INDEX idx_records_ws_type");con.executescript(downgrade);con.close()
    before=inspect_m1(journal);assert before["record_count"]==55 and not before["already_m1"]
    def projected(path):
        retrieval=tmp_path/path;projector=ProjectionProjector(ProjectorConfig(journal,retrieval,"fake",4,"fake"),FakeEmbedder())
        while projector.run_once(100).status==ProjectionStatus.HEALTHY:pass
        db=sqlite3.connect(retrieval)
        return db.execute("SELECT record_id,projection_hash FROM retrieval_documents ORDER BY record_id").fetchall()
    hashes_before=projected("before-migration.db")
    backup=tmp_path/"representative.backup.db";report=migrate_m1(journal,backup);after=inspect_m1(journal)
    assert report["changed"] and before["table_digests"]==after["table_digests"]
    assert projected("after-migration.db")==hashes_before
    rerun=migrate_m1(journal,backup)
    assert not rerun["changed"] and rerun["message"]=="already migrated"

# --- Package 4: canonical write-envelope filtering (closes B6 + B7) ---

def test_write_envelope_field_classification_lock_in():
    """§7.1 lock-in: every request-model field is classified, and adding a
    new field without classifying it here must fail this test."""
    assert set(REQUEST_MODELS) == set(FIELD_CLASSIFICATION)
    for name, model in REQUEST_MODELS.items():
        declared = set(FIELD_CLASSIFICATION[name])
        actual = set(model.model_fields)
        assert actual == declared, f"{name}: unclassified or stale fields {actual ^ declared}"
        assert all(v in CLASSIFICATIONS for v in FIELD_CLASSIFICATION[name].values())
    assert set(OUT_OF_BAND_FIELDS) == {"request_id"}
    assert all(v in CLASSIFICATIONS for v in OUT_OF_BAND_FIELDS.values())

def test_collect_client_strings_walks_keys_values_and_nesting():
    """B6/row 21: the one canonical scanner walk must reach dict keys and
    values at any depth, including a dict nested inside a list and a dict
    used as a mapping (its own keys) nested inside a list."""
    structure = {
        "marker-key": "marker-value",
        "list_of_dicts": [{"nested-key": "x"}, "plain-item", ["inner-list-item", {"listed-key": "y"}]],
    }
    collected = collect_client_strings(structure)
    for expected in ("marker-key", "marker-value", "nested-key", "listed-key", "plain-item", "inner-list-item"):
        assert expected in collected

def test_b6_dict_key_and_nested_secrets_are_rejected_by_band_c():
    from brain.write_policy import enforce_band_c
    with pytest.raises(BrainError, match="BRAIN_WRITE_SECRET_REJECTED"):
        enforce_band_c({"items": [{KEY_SAFE_CANARY: "value"}]}, {}, "test-request")
    with pytest.raises(BrainError, match="BRAIN_WRITE_SECRET_REJECTED"):
        enforce_band_c({"items": [{"k": KEY_SAFE_CANARY}]}, {}, "test-request")
    with pytest.raises(BrainError, match="BRAIN_WRITE_SECRET_REJECTED"):
        enforce_band_c({KEY_SAFE_CANARY: "value"}, {}, "test-request")

def test_b6_probe_rerun_verification_key_secret_is_shape_safe_is_rejected(tmp_path):
    """B6 baseline probe re-run: verification={"api_key=sk-live-<canary>": "ok"}
    used to persist. A key without '=' still passes the new shape gate, so
    this proves the dict-key Band C scan itself closes the gap."""
    b,journal=brain(tmp_path)
    before=row_counts(journal)
    with pytest.raises(BrainError, match="BRAIN_WRITE_SECRET_REJECTED"):
        b.record_outcome(**outcome(verification={KEY_SAFE_CANARY: "ok"}, idempotency_key="b6-probe-key-safe"))
    assert row_counts(journal) == before

def test_b6_probe_rerun_verification_key_with_equals_is_rejected_without_leaking(tmp_path):
    """The literal B6 baseline probe: verification={"api_key=sk-live-...": "ok"}.
    The '=' violates the new verification-key shape pattern, so this is
    rejected at the pydantic layer -- assert the canary never reaches the
    returned error JSON despite that layer's default value-echoing behavior."""
    b,journal=brain(tmp_path)
    before=row_counts(journal)
    with pytest.raises(BrainError) as exc_info:
        b.record_outcome(**outcome(verification={CANARY: "ok"}, idempotency_key="b6-probe-key-equals"))
    err=exc_info.value
    assert err.code == "BRAIN_INVALID_REQUEST"
    assert CANARY not in str(err) and "sk-live" not in str(err)
    assert CANARY not in json.dumps(err.details) and "sk-live" not in json.dumps(err.details)
    assert row_counts(journal) == before

def test_verification_key_shape_constraint_rejects_malformed_keys(tmp_path):
    b,journal=brain(tmp_path)
    valid=b.record_outcome(**outcome(verification={"tests/passed": "yes", "step.1": "ok", "run_id":"a-b:c"},
                                     idempotency_key="verification-key-valid"))
    assert valid.status == "accepted"
    for bad_key in ("", "x"*101, "has a tab\t", "semi;colon", "pipe|char"):
        with pytest.raises(BrainError, match="BRAIN_INVALID_REQUEST"):
            b.record_outcome(**outcome(verification={bad_key: "v"}, idempotency_key=f"verification-key-bad-{hash(bad_key)}"))

def test_request_id_shape_contract(tmp_path):
    b,journal=brain(tmp_path)
    valid=b.record_outcome(**outcome(idempotency_key="request-id-valid"), request_id="agent-run.7:2026-07-16")
    assert valid.request_id == "agent-run.7:2026-07-16"
    before=row_counts(journal)
    invalid_ids = ("", " ", "id with spaces", "x"*129, "semi;colon", CANARY)
    for bad in invalid_ids:
        with pytest.raises(BrainError) as exc_info:
            b.record_outcome(**outcome(idempotency_key=f"request-id-bad-{hash(bad)}"), request_id=bad)
        err=exc_info.value
        assert err.code == "BRAIN_INVALID_REQUEST"
        assert err.request_id == ""
        if len(bad) > 1: assert bad not in str(err)
        assert CANARY not in str(err) and "sk-live" not in str(err)
    assert row_counts(journal) == before

def test_b7_probe_rerun_request_id_canary_is_rejected_before_any_write(tmp_path):
    b,journal,audit_log=brain_with_audit(tmp_path)
    before=row_counts(journal)
    with pytest.raises(BrainError) as exc_info:
        b.record_outcome(**outcome(idempotency_key="b7-probe"), request_id=CANARY)
    err=exc_info.value
    assert err.code == "BRAIN_INVALID_REQUEST" and err.request_id == ""
    assert row_counts(journal) == before
    audit = audit_bytes(audit_log)
    assert CANARY.encode() not in audit and b"sk-live" not in audit

def test_secret_non_persistence_matrix(tmp_path):
    """§10 row 20: a client-controlled secret in any persisted write field is
    rejected, and the canary bytes are absent from the journal file, the
    audit log, and the returned error."""
    b,journal,audit_log=brain_with_audit(tmp_path)

    def outcome_case(**overrides):
        def run():
            return b.record_outcome(**outcome(**overrides))
        return run

    def decision_case(**overrides):
        def run():
            return b.record_decision(**decision(**overrides))
        return run

    def problem_case(**overrides):
        def run():
            return b.record_problem(**problem(**overrides))
        return run

    cases = {
        "summary": outcome_case(summary=CANARY, idempotency_key="secret-summary"),
        "changes": outcome_case(changes=[CANARY], idempotency_key="secret-changes"),
        "verification_value": outcome_case(verification={"result": CANARY}, idempotency_key="secret-verification-value"),
        "verification_key": outcome_case(verification={KEY_SAFE_CANARY: "ok"}, idempotency_key="secret-verification-key"),
        "open_questions": outcome_case(open_questions=[CANARY], idempotency_key="secret-open-questions"),
        "statement": decision_case(statement=CANARY, idempotency_key="secret-statement"),
        "rationale": decision_case(rationale=CANARY, idempotency_key="secret-rationale"),
        "alternatives_reason": decision_case(alternatives=[{"option":"x","verdict":"rejected","reason":CANARY}],
                                              idempotency_key="secret-alt-reason"),
        "alternatives_evidence": decision_case(alternatives=[{"option":"x","verdict":"rejected","reason":"ok",
                                              "evidence":[uri_canary()]}], idempotency_key="secret-alt-evidence"),
        "evidence": problem_case(evidence=[uri_canary()], idempotency_key="secret-evidence"),
        "artifacts": outcome_case(artifacts=[uri_canary()], idempotency_key="secret-artifacts"),
        "commit": outcome_case(artifacts=[], commit=f"git://repo/commit/{KEY_SAFE_CANARY}", idempotency_key="secret-commit"),
        "source_excerpt": outcome_case(source_excerpt=CANARY, idempotency_key="secret-source-excerpt"),
        "source_ref": outcome_case(source_ref=CANARY, idempotency_key="secret-source-ref"),
        "session_ref": outcome_case(session_ref=CANARY, idempotency_key="secret-session-ref"),
        "change_reason": outcome_case(supersedes="rec-does-not-exist", change_reason=CANARY, idempotency_key="secret-change-reason"),
        "idempotency_key": outcome_case(idempotency_key=CANARY),
    }

    for label, run in cases.items():
        before = row_counts(journal)
        with pytest.raises(BrainError) as exc_info:
            run()
        err = exc_info.value
        assert CANARY not in str(err), f"{label}: canary leaked in error str"
        assert CANARY not in json.dumps(err.details), f"{label}: canary leaked in error details"
        assert row_counts(journal) == before, f"{label}: rejected write left persistent rows"

    journal_bytes = Path(journal).read_bytes()
    assert CANARY.encode() not in journal_bytes and b"sk-live" not in journal_bytes
    audit = audit_bytes(audit_log)
    assert CANARY.encode() not in audit and b"sk-live" not in audit

    # request_id: handled separately since it is out-of-band (not Band C
    # scanned), but must still be rejected before any journal/audit write.
    before = row_counts(journal)
    with pytest.raises(BrainError) as exc_info:
        b.record_outcome(**outcome(idempotency_key="secret-request-id"), request_id=CANARY)
    err = exc_info.value
    assert err.code == "BRAIN_INVALID_REQUEST" and err.request_id == ""
    assert row_counts(journal) == before

def test_f1_sanitized_write_error_carries_no_exception_context(tmp_path):
    """F1: `raise ... from None` only suppresses the chain in default
    renderers -- __context__ is still populated with the raw pydantic
    ValidationError, which itself echoes the offending secret verification
    key (see test_b6_probe_rerun_verification_key_with_equals_is_rejected_
    without_leaking for that echo). A debugger or error-reporting SDK that
    walks __context__/__cause__ unconditionally, ignoring __suppress_
    context__, would still re-expose the canary. The sanitized BrainError
    must carry no reference to the original exception at all."""
    b,journal=brain(tmp_path)
    before=row_counts(journal)
    with pytest.raises(BrainError) as exc_info:
        b.record_outcome(**outcome(verification={CANARY: "ok"}, idempotency_key="f1-context-probe"))
    err=exc_info.value
    assert err.code == "BRAIN_INVALID_REQUEST"
    assert err.__context__ is None, f"__context__ still references {err.__context__!r}"
    assert err.__cause__ is None
    tb_text = "".join(traceback.format_exception(type(err), err, err.__traceback__))
    for surface in (str(err), repr(err), tb_text, json.dumps(err.details)):
        assert CANARY not in surface and "sk-live" not in surface
    assert row_counts(journal) == before

def test_f2_bare_secret_in_artifact_fields_is_secret_rejected_not_uri_echoed(tmp_path):
    """F2: validate_evidence_uris used to run before enforce_band_c, so a
    bare (non-URI-shaped) secret in evidence[]/artifacts[]/commit/
    alternatives[].evidence[] failed URI syntax first and echoed the raw
    value verbatim in details.values (BRAIN_INVALID_ARTIFACT_URI). Band C
    now runs first over the whole write envelope, so every position here
    must be rejected as BRAIN_WRITE_SECRET_REJECTED with nothing echoed."""
    b,journal,audit_log=brain_with_audit(tmp_path)

    cases = {
        "evidence": lambda: b.record_problem(**problem(evidence=[CANARY], idempotency_key="f2-evidence")),
        "artifacts": lambda: b.record_outcome(**outcome(artifacts=[CANARY], idempotency_key="f2-artifacts")),
        "commit": lambda: b.record_outcome(**outcome(artifacts=[], commit=CANARY, idempotency_key="f2-commit")),
        "alternatives_evidence": lambda: b.record_decision(**decision(
            alternatives=[{"option":"x","verdict":"rejected","reason":"ok","evidence":[CANARY]}],
            idempotency_key="f2-alt-evidence")),
    }

    for label, run in cases.items():
        before=row_counts(journal)
        with pytest.raises(BrainError) as exc_info:
            run()
        err=exc_info.value
        assert err.code == "BRAIN_WRITE_SECRET_REJECTED", f"{label}: got {err.code} (details={err.details})"
        assert CANARY not in str(err) and CANARY not in repr(err), f"{label}: canary leaked in error text"
        assert CANARY not in json.dumps(err.details), f"{label}: canary leaked in error details"
        assert err.__context__ is None, f"{label}: __context__ still references {err.__context__!r}"
        assert row_counts(journal) == before, f"{label}: rejected write left persistent rows"

    journal_bytes = Path(journal).read_bytes()
    assert CANARY.encode() not in journal_bytes and b"sk-live" not in journal_bytes
    audit = audit_bytes(audit_log)
    assert CANARY.encode() not in audit and b"sk-live" not in audit

# ---------------------------------------------------------------------------
# Package 6 (closes B9, §8 artifact trust model, §10 rows 23-24): a
# syntactically valid artifact URI is never itself proof; only a server-side
# verify_all() hit earns verified_active/verified_inactive, everything else
# (including "nobody looked") reads back as unverified_reference. Evidence
# URIs now go through the same server verification as artifacts[], but must
# never change which band a write lands in.
# ---------------------------------------------------------------------------

def trust_for(related_rows, uri):
    return next(row for row in related_rows if row.get("artifact_uri") == uri)["artifact_trust"]

def test_repo_artifact_exists_surfaces_verified_active_with_verifier_metadata(tmp_path):
    b, journal = brain(tmp_path)
    rec = b.record_outcome(**outcome(idempotency_key="trust-repo-exists"))
    uri = "repo://pavol-brain/brain/api.py"
    trust = trust_for(b.get_related(rec.record_id, allowed_workspaces=["personal"]).related, uri)
    assert trust["state"] == "verified_active"
    assert trust["method"] == "git_ls_files"
    assert trust["verifier"] == "server-artifact-validator"
    assert trust["verified_at"]
    assert trust["digest"] and len(trust["digest"]) == 40
    assert trust["reason"] is None

def test_git_commit_artifact_exists_surfaces_verified_active(tmp_path):
    b, journal = brain(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    uri = f"git://pavol-brain/commit/{head}"
    rec = b.record_outcome(**outcome(artifacts=[], commit=uri, idempotency_key="trust-git-exists"))
    trust = trust_for(b.get_related(rec.record_id, allowed_workspaces=["personal"]).related, uri)
    assert trust["state"] == "verified_active"
    assert trust["method"] == "git_cat_file"
    assert trust["digest"] == head

def test_nonexistent_repo_and_git_artifacts_are_verified_inactive(tmp_path):
    b, journal = brain(tmp_path)
    missing_repo = "repo://pavol-brain/does-not-exist"
    missing_commit = "git://pavol-brain/commit/" + "0" * 40
    # source_assertion=explicit_user_confirmation so the record lands Band A
    # (accepted) regardless of artifact validity -- get_record/get_related
    # refuse candidate rows, and this test is about the trust view, not band
    # classification (see test_evidence_verification_does_not_change_band_
    # classification for that).
    rec = b.record_outcome(**outcome(artifacts=[missing_repo, missing_commit], commit=None,
                                      source_assertion="explicit_user_confirmation", idempotency_key="trust-missing"))
    related = b.get_related(rec.record_id, allowed_workspaces=["personal"]).related
    for uri in (missing_repo, missing_commit):
        trust = trust_for(related, uri)
        assert trust["state"] == "verified_inactive"
        assert trust["digest"] is None

def test_doc_scheme_artifact_is_unverified_reference(tmp_path):
    b, journal = brain(tmp_path)
    uri = "doc://synthetic/unverified"
    rec = b.record_outcome(**outcome(artifacts=[uri], source_assertion="explicit_user_confirmation", idempotency_key="trust-doc"))
    trust = trust_for(b.get_related(rec.record_id, allowed_workspaces=["personal"]).related, uri)
    assert trust["state"] == "unverified_reference"
    assert trust["method"] == "not_deterministically_verifiable"
    assert trust["reason"] == "not_deterministically_verifiable"
    assert trust["digest"] is None

def test_unknown_repo_alias_is_unverified_reference_without_path_leak(tmp_path):
    b, journal = brain(tmp_path)
    uri = "repo://ghost-repo/some/file.py"
    rec = b.record_outcome(**outcome(artifacts=[uri], source_assertion="explicit_user_confirmation", idempotency_key="trust-unknown-alias"))
    related = b.get_related(rec.record_id, allowed_workspaces=["personal"]).related
    trust = trust_for(related, uri)
    assert trust["state"] == "unverified_reference"
    assert trust["method"] == "repo_unavailable"
    payload = json.dumps(related)
    assert str(ROOT) not in payload and "/Users/" not in payload

def test_evidence_repo_uri_is_server_verified_not_blanket_derived(tmp_path):
    b, journal = brain(tmp_path)
    uri = "repo://pavol-brain/brain/api.py"
    rec = b.record_problem(statement="evidence gets verified", impact="test", evidence=[uri],
                            source_assertion="explicit_user_confirmation", workspace="personal",
                            idempotency_key="trust-evidence-repo")
    con = sqlite3.connect(journal); con.row_factory = sqlite3.Row
    origin = con.execute("SELECT origin FROM artifact_links WHERE record_id=? AND artifact_uri=?", (rec.record_id, uri)).fetchone()["origin"]
    assert origin == "deterministic"
    event = con.execute("SELECT state,evidence FROM artifact_validation_events WHERE artifact_record_id=? AND artifact_uri=?",
                         (rec.record_id, uri)).fetchone()
    assert event["state"] == "verified_active"
    meta = json.loads(event["evidence"])
    assert meta["verifier"] == "server-artifact-validator" and meta["object_digest"]
    trust = trust_for(b.get_related(rec.record_id, allowed_workspaces=["personal"]).related, uri)
    assert trust["state"] == "verified_active"

def test_evidence_doc_uri_stays_unverified_reference(tmp_path):
    b, journal = brain(tmp_path)
    uri = "doc://synthetic/evidence-unverified"
    rec = b.record_problem(statement="evidence stays unverified", impact="test", evidence=[uri],
                            source_assertion="explicit_user_confirmation", workspace="personal",
                            idempotency_key="trust-evidence-doc")
    trust = trust_for(b.get_related(rec.record_id, allowed_workspaces=["personal"]).related, uri)
    assert trust["state"] == "unverified_reference"

def test_evidence_verification_does_not_change_band_classification(tmp_path):
    # §8: evidence verification "can only gain honesty" -- classify() keeps
    # reading only artifacts+commit, so an identical request differing only
    # in whether its evidence happens to be server-verifiable must land in
    # the same band. Covers both a Band A and a Band B source_assertion.
    b, journal = brain(tmp_path)
    verifiable = b.record_problem(statement="band regression verifiable", impact="test",
                                   evidence=["repo://pavol-brain/brain/api.py"],
                                   source_assertion="explicit_user_confirmation", workspace="personal",
                                   idempotency_key="band-a-verifiable-evidence")
    unverifiable = b.record_problem(statement="band regression unverifiable", impact="test",
                                     evidence=["doc://synthetic/band-a"],
                                     source_assertion="explicit_user_confirmation", workspace="personal",
                                     idempotency_key="band-a-unverifiable-evidence")
    assert (verifiable.policy_band, verifiable.status) == (unverifiable.policy_band, unverifiable.status) == ("A", "accepted")

    verifiable_b = b.record_problem(statement="band b regression verifiable", impact="test",
                                     evidence=["repo://pavol-brain/brain/api.py"],
                                     source_assertion="agent_inference", workspace="personal",
                                     idempotency_key="band-b-verifiable-evidence")
    unverifiable_b = b.record_problem(statement="band b regression unverifiable", impact="test",
                                       evidence=["doc://synthetic/band-b"],
                                       source_assertion="agent_inference", workspace="personal",
                                       idempotency_key="band-b-unverifiable-evidence")
    assert (verifiable_b.policy_band, verifiable_b.status) == (unverifiable_b.policy_band, unverifiable_b.status) == ("B", "candidate")

def test_artifacts_only_band_a_gate_unaffected_by_package_6(tmp_path):
    # Pins the pre-existing §10 acceptance behaviour (test_artifact_validation_
    # controls_band_a_and_writes_audit_events) still holds byte-for-byte after
    # evidence verification was added: artifacts[] is still the only input to
    # classify() for outcome+verified_tool_result.
    b, journal = brain(tmp_path)
    valid = b.record_outcome(**outcome(idempotency_key="p6-valid-artifact"))
    missing = b.record_outcome(**outcome(artifacts=["repo://pavol-brain/does-not-exist"], idempotency_key="p6-missing-artifact"))
    document = b.record_outcome(**outcome(artifacts=["doc://synthetic/unverified"], idempotency_key="p6-doc-artifact"))
    assert valid.status == "accepted" and valid.policy_band == "A"
    assert missing.status == document.status == "candidate"
    assert missing.policy_band == document.policy_band == "B"

def test_client_cannot_self_assert_trust_fields_on_any_request_model(tmp_path):
    b, journal = brain(tmp_path)
    before = row_counts(journal)
    forbidden = {"verified": True, "verification_state": "verified_active", "artifact_trust": {"state": "verified_active"},
                 "verifier": "server-artifact-validator", "verified_at": "2026-07-16T00:00:00+00:00", "digest": "deadbeef"}
    for field, value in forbidden.items():
        with pytest.raises(BrainError, match="BRAIN_INVALID_REQUEST"):
            b.record_problem(**problem(idempotency_key=f"self-assert-{field}", **{field: value}))
    assert row_counts(journal) == before

def test_request_models_reject_trust_fields_at_the_schema_level():
    from brain.models import OutcomeRequest, DecisionRequest, ProblemRequest, AnalysisRequest
    from pydantic import ValidationError
    forbidden = ("verified", "verification_state", "artifact_trust", "verifier", "verified_at", "digest")
    cases = (
        (OutcomeRequest, {"workspace": "personal", "summary": "x"}),
        (DecisionRequest, {"workspace": "personal", "statement": "x", "rationale": "y", "reason": "z"}),
        (ProblemRequest, {"workspace": "personal", "statement": "x", "impact": "y"}),
        (AnalysisRequest, {"workspace": "personal", "summary": "x", "findings": ["y"]}),
    )
    for model, base in cases:
        model(**base)  # sanity: the base payload alone is valid
        for field in forbidden:
            with pytest.raises(ValidationError):
                model(**base, **{field: "x"})

def test_related_row_missing_validation_state_fails_safe_to_unverified_reference(tmp_path):
    # §8: "no client-reachable field can set or influence any validation
    # state" plus the fail-safe requirement -- an artifact_links row with no
    # matching artifact_validation_state (never verified by this write, or a
    # legacy/corrupt row) must present as unverified_reference, never as
    # verified by omission.
    b, journal = brain(tmp_path)
    rec = b.record_problem(statement="orphan link", impact="test", source_assertion="explicit_user_confirmation", workspace="personal")
    con = sqlite3.connect(journal)
    con.execute("INSERT INTO artifact_links VALUES (?,?,?,?,?,?,?)",
                (rec.record_id, "repo://pavol-brain/orphan-link.py", "touches", 1.0, "corrupt-fixture", "2026-07-16T00:00:00+00:00", 1))
    con.commit(); con.close()
    trust = trust_for(b.get_related(rec.record_id, allowed_workspaces=["personal"]).related, "repo://pavol-brain/orphan-link.py")
    assert trust == {"state": "unverified_reference", "method": None, "verifier": None,
                      "verified_at": None, "digest": None, "reason": None}

def test_search_include_artifacts_carries_the_same_trust_view_as_get_related(tmp_path):
    b, journal = brain(tmp_path)
    uri = "repo://pavol-brain/brain/api.py"
    problem_rec = b.record_problem(statement="search trust parity", impact="test", evidence=[uri],
                                    source_assertion="explicit_user_confirmation", workspace="personal")
    retrieval = tmp_path / "retrieval.db"
    projector = ProjectionProjector(ProjectorConfig(journal, retrieval, "fake", 4, "fake", instance_id="personal"), FakeEmbedder())
    while projector.run_once(100).status == ProjectionStatus.HEALTHY: pass
    scoped = Brain(BrainConfig(journal_db_path=journal, retrieval_db_path=retrieval, embedding_dimension=4,
                                endpoint_probe_timeout=.01, client_identity="reader", instance_id="personal"), NoopTransport())
    result = scoped.search(query="search trust parity", workspaces=["personal"], types=["problem"], limit=10, include_artifacts=True)
    row = next(item for item in result.results if item.record_id == problem_rec.record_id)
    trust = trust_for(row.artifact_links, uri)
    assert trust["state"] == "verified_active"
    assert trust["verifier"] == "server-artifact-validator"
