import hashlib
import json
import sqlite3
import sys
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
from journal_fixture import journal_fixture
from src.journal import fold

class NoopTransport:
    def embed(self,text):return [1.0,0.0,0.0,0.0]

class FakeEmbedder:
    def embed_document(self,text):
        seed=int(hashlib.sha256(text.encode()).hexdigest()[:8],16)
        return [float((seed>>(i*4))%11+1) for i in range(4)],"fake"

def brain(tmp_path,identity="writer"):
    journal=tmp_path/"journal.db";journal_fixture(journal)
    config=BrainConfig(journal_db_path=journal,retrieval_db_path=tmp_path/"retrieval.db",embedding_dimension=4,
                       endpoint_probe_timeout=.01,client_identity=identity,instance_id="personal")
    return Brain(config,NoopTransport()),journal

def attached_brain(journal,tmp_path,identity,instance="personal"):
    return Brain(BrainConfig(journal_db_path=journal,retrieval_db_path=tmp_path/f"{identity}.retrieval.db",embedding_dimension=4,
                             endpoint_probe_timeout=.01,client_identity=identity,instance_id=instance),NoopTransport())

def outcome(**overrides):
    return {"summary":"Implemented M1 write path","changes":["added writer"],"verification":{"tests":"pass"},
            "artifacts":["repo://pavol-brain/brain/api.py"],"source_assertion":"verified_tool_result",
            "workspace":"personal",**overrides}

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

def test_instance_namespace_and_library_mapping_are_enforced(tmp_path):
    personal_dir=tmp_path/"personal-instance";work_dir=tmp_path/"work-instance";personal_dir.mkdir();work_dir.mkdir()
    personal,pjournal=brain(personal_dir,"same-agent")
    _,wjournal=brain(work_dir,"seed");work=attached_brain(wjournal,work_dir,"same-agent","work")
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
    incoming=b.get_related(problem.record_id).related
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
    retrieval=tmp_path/"retrieval-scope.db";projector=ProjectionProjector(ProjectorConfig(journal,retrieval,"fake",4,"fake"),FakeEmbedder())
    while projector.run_once(100).status==ProjectionStatus.HEALTHY:pass
    scoped=Brain(BrainConfig(journal_db_path=journal,retrieval_db_path=retrieval,embedding_dimension=4,
                             endpoint_probe_timeout=.01,client_identity="reader",instance_id="personal"),NoopTransport())
    result=scoped.search(query="Scoped source",workspaces=["personal"],types=["problem"],limit=10,include_artifacts=True)
    row=next(item for item in result.results if item.record_id==personal.record_id)
    assert not any(link.get("record_id")==foreign.record_id for link in row.artifact_links)

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
