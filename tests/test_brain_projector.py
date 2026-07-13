import gc
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "sqlite-spike" / "scripts")); sys.path.insert(0, str(ROOT / "tests"))
from brain import artifact_validation as av
from brain.projector import ProjectorConfig, ProjectionProjector
from brain.projector.journal_reader import JournalReader, sha256
from brain.projector.models import ProjectionStatus
from journal_fixture import journal_fixture, add_validation_event


class FakeEmbedder:
    def __init__(self, dimension=4): self.dimension = dimension; self.calls = 0
    def embed_document(self, text):
        self.calls += 1
        seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
        return [float((seed >> (8 * i)) % 17 + 1) for i in range(self.dimension)], "fake-embedder"


class ProjectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.journal = root / "journal.db"; self.retrieval = root / "retrieval.db"; journal_fixture(self.journal)
        self.embedder = FakeEmbedder(); self.config = ProjectorConfig(self.journal, self.retrieval, "fake-fingerprint", 4, "fake-embedder")
        self.projector = ProjectionProjector(self.config, self.embedder)
    def tearDown(self):
        gc.collect()
        self.tmp.cleanup()
    def _full(self):
        while self.projector.run_once(100).status == ProjectionStatus.HEALTHY: pass
    def _counts(self):
        con=sqlite3.connect(self.retrieval); return con.execute("SELECT count(*) FROM retrieval_documents").fetchone()[0], con.execute("SELECT count(*) FROM retrieval_embeddings").fetchone()[0]

    def test_empty_cursor_full_eligible_projection(self):
        report=self.projector.run_once(100); self.assertEqual(report.status, ProjectionStatus.HEALTHY); self.assertEqual(self._counts(), (51,51))
    def test_no_new_events_is_no_changes(self): self._full(); self.assertEqual(self.projector.run_once().status, ProjectionStatus.NO_CHANGES)
    def test_unchanged_hash_reuses_embedding_on_replay(self):
        self._full(); calls=self.embedder.calls; self.assertEqual(self.projector.run_once().embeddings_created,0); self.assertEqual(self.embedder.calls,calls)
    def test_forbidden_records_are_excluded(self):
        self._full(); con=sqlite3.connect(self.retrieval); self.assertEqual(con.execute("SELECT count(*) FROM retrieval_documents WHERE status IN ('candidate','rejected','forgotten')").fetchone()[0],0)
    def test_superseded_is_historical_not_current(self):
        self._full(); con=sqlite3.connect(self.retrieval); row=con.execute("SELECT status,is_current,invalid_at,superseded_by FROM retrieval_documents WHERE record_id='rec-045'").fetchone(); self.assertEqual(row[0],"superseded"); self.assertEqual(row[1],0); self.assertTrue(row[2]); self.assertEqual(row[3],"rec-046")
    def test_artifact_links_are_typed_and_deduped(self):
        self._full(); con=sqlite3.connect(self.retrieval)
        self.assertEqual(con.execute("SELECT count(*) FROM retrieval_document_links").fetchone()[0],13)
        self.assertEqual({r[0] for r in con.execute("SELECT DISTINCT origin FROM retrieval_document_links")},{"canonical_validation"})
    def test_duplicate_event_replay_has_no_duplicates(self):
        self._full(); con=sqlite3.connect(self.journal); row=con.execute("SELECT * FROM memory_events WHERE record_id='rec-001'").fetchone(); con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)", ("evt-9999","rec-001","record_approved","2026-08-01T00:00:00+00:00","fixture",row[5])); con.commit(); report=self.projector.run_once(); self.assertEqual(report.inserted,0); self.assertEqual(self._counts(),(51,51))
    def test_changed_projection_reembeds(self):
        self._full(); before=self.embedder.calls; con=sqlite3.connect(self.journal); con.execute("UPDATE memory_records SET payload=? WHERE record_id='rec-001'", (json.dumps({"statement":"changed","rationale":"x","decision_status":"accepted"}),)); con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)", ("evt-9998","rec-001","record_approved","2026-08-02T00:00:00+00:00","fixture","{}")); con.commit(); report=self.projector.run_once(); self.assertEqual(report.embeddings_created,1); self.assertEqual(self.embedder.calls,before+1)
    def test_one_new_accepted_record_is_inserted(self):
        self._full(); con=sqlite3.connect(self.journal); payload=json.dumps({"subject":"slice2","predicate":"has","object":"incremental projection","evidence":"fixture"})
        con.execute("INSERT INTO memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("rec-new",1,"fact","ai-pos","normal",payload,payload,"hash","idempotency-new","fixture","imported_curated",None,None,None,1.0,"2026-08-03T00:00:00+00:00","2026-08-03T00:00:00+00:00"))
        con.execute("INSERT INTO record_state VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("rec-new","accepted","human_approved",None,None,None,None,"none",None,None,"evt-new")); con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)", ("evt-new","rec-new","record_created","2026-08-03T00:00:00+00:00","fixture","{}")); con.commit()
        report=self.projector.run_once(); self.assertEqual(report.inserted,1); self.assertEqual(self._counts(),(52,52))

    def test_v2_outcome_cannot_advance_cursor_without_projection(self):
        self._full(); con=sqlite3.connect(self.journal)
        payload=json.dumps({"summary":"M1 client outcome","changes":["configured"],"verification":{"tests":"pass"},"open_questions":[],"artifacts":[],"commit":None})
        values=("rec-v2",2,"outcome","personal","normal",payload,payload,"hash-v2","idempotency-v2","hermes-personal","explicit_user_confirmation",None,None,None,1.0,"2026-08-03T01:00:00+00:00","2026-08-03T01:00:00+00:00")
        con.execute("INSERT INTO memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",values)
        con.execute("INSERT INTO record_state VALUES (?,?,?,?,?,?,?,?,?,?,?)",("rec-v2","accepted","auto_accepted",None,None,None,None,"none",None,None,"evt-v2"))
        con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)",("evt-v2","rec-v2","record_created","2026-08-03T01:00:00+00:00","hermes-personal","{}"));con.commit();con.close()
        before=sqlite3.connect(self.retrieval).execute("SELECT last_source_event_id FROM retrieval_projection_cursor WHERE singleton=1").fetchone()[0]
        class SilentSkipProjector(ProjectionProjector):
            def _upsert(self, con, doc, report): return "silently_skipped"
        with self.assertRaisesRegex(Exception,"accepted_record_missing_document"):
            SilentSkipProjector(self.config,self.embedder).run_once()
        check=sqlite3.connect(self.retrieval)
        self.assertEqual(check.execute("SELECT last_source_event_id FROM retrieval_projection_cursor WHERE singleton=1").fetchone()[0],before)
        self.assertIsNone(check.execute("SELECT record_id FROM retrieval_documents WHERE record_id='rec-v2'").fetchone())
        report=self.projector.run_once();self.assertEqual(report.inserted,1)
        self.assertEqual(report.details["record_outcomes"],[{"record_id":"rec-v2","result":"projected","action":"inserted","projection_hash":check.execute("SELECT projection_hash FROM retrieval_documents WHERE record_id='rec-v2'").fetchone()[0]}])

    def test_missing_record_event_blocks_cursor_and_cli_exits_nonzero(self):
        self._full(); con=sqlite3.connect(self.journal)
        con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)",("evt-missing","rec-missing","record_created","2026-08-03T02:00:00+00:00","fixture","{}"));con.commit();con.close()
        before=sqlite3.connect(self.retrieval).execute("SELECT last_source_event_id FROM retrieval_projection_cursor WHERE singleton=1").fetchone()[0]
        report=self.projector.run_once();self.assertEqual(report.status,ProjectionStatus.REBUILD_REQUIRED)
        self.assertEqual(report.details,{"issues":["missing_record_snapshot"],"record_ids":["rec-missing"]})
        self.assertEqual(sqlite3.connect(self.retrieval).execute("SELECT last_source_event_id FROM retrieval_projection_cursor WHERE singleton=1").fetchone()[0],before)
        result=subprocess.run([sys.executable,str(ROOT/"scripts/run_brain_projector.py"),"--journal-db",str(self.journal),"--retrieval-db",str(self.retrieval),"--run-once"],capture_output=True,text=True)
        self.assertEqual(result.returncode,2);self.assertIn('"status": "REBUILD_REQUIRED"',result.stdout)

    def test_ineligible_skip_has_deterministic_audit_reason(self):
        self._full(); con=sqlite3.connect(self.journal)
        con.execute("UPDATE record_state SET status='forgotten',updated_event_id='evt-audit-skip' WHERE record_id='rec-001'")
        con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)",("evt-audit-skip","rec-001","record_forgotten","2026-08-03T03:00:00+00:00","fixture","{}"));con.commit();con.close()
        report=self.projector.run_once()
        self.assertEqual(report.details["record_outcomes"],[{"record_id":"rec-001","result":"skipped","reason":"status_forgotten","action":"removed"}])
    def test_forget_removes_existing_document(self):
        self._full(); con=sqlite3.connect(self.journal); con.execute("UPDATE record_state SET status='forgotten',updated_event_id='evt-forget' WHERE record_id='rec-001'"); con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)", ("evt-forget","rec-001","record_forgotten","2026-08-04T00:00:00+00:00","fixture","{}")); con.commit()
        report=self.projector.run_once(); self.assertEqual(report.removed,1); self.assertEqual(self._counts(),(50,50))
    def test_reject_removes_existing_document(self):
        self._full(); con=sqlite3.connect(self.journal); con.execute("UPDATE record_state SET status='rejected',updated_event_id='evt-reject' WHERE record_id='rec-001'"); con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)", ("evt-reject","rec-001","record_rejected","2026-08-05T00:00:00+00:00","fixture","{}")); con.commit()
        report=self.projector.run_once(); self.assertEqual(report.removed,1); self.assertEqual(self._counts(),(50,50))
    def test_failure_points_roll_back_and_retry(self):
        for point in ("after_batch_read","after_documents","after_embeddings","before_cursor_update","before_commit"):
            with self.subTest(point=point):
                tmp=Path(self.tmp.name)/(point+".db"); cfg=ProjectorConfig(self.journal,tmp,"fake-fingerprint",4,"fake")
                def fail(got):
                    if got==point: raise RuntimeError(point)
                bad=ProjectionProjector(cfg,FakeEmbedder(),fail)
                with self.assertRaises(Exception): bad.run_once()
                if tmp.exists(): self.assertIsNone(sqlite3.connect(tmp).execute("SELECT last_source_event_id FROM retrieval_projection_cursor WHERE singleton=1").fetchone())
                good=ProjectionProjector(cfg,FakeEmbedder()); self.assertEqual(good.run_once().status,ProjectionStatus.HEALTHY)
    def test_model_mismatch_requires_rebuild(self):
        self._full(); changed=ProjectionProjector(ProjectorConfig(self.journal,self.retrieval,"other",4,"fake"),self.embedder); self.assertEqual(changed.run_once().status,ProjectionStatus.REBUILD_REQUIRED)
    def test_dimension_mismatch_requires_rebuild(self):
        self._full(); changed=ProjectionProjector(ProjectorConfig(self.journal,self.retrieval,"fake-fingerprint",5,"fake"),FakeEmbedder(5)); self.assertEqual(changed.run_once().status,ProjectionStatus.REBUILD_REQUIRED)
    def test_schema_mismatch_requires_rebuild(self):
        self._full(); changed=ProjectionProjector(ProjectorConfig(self.journal,self.retrieval,"fake-fingerprint",4,"fake",projection_schema_version="v2"),self.embedder); self.assertEqual(changed.run_once().status,ProjectionStatus.REBUILD_REQUIRED)
    def test_cursor_ahead_requires_rebuild(self):
        self._full(); con=sqlite3.connect(self.retrieval); con.execute("UPDATE retrieval_projection_cursor SET last_source_event_id='9999'"); con.commit(); self.assertEqual(self.projector.run_once().status,ProjectionStatus.REBUILD_REQUIRED)
    def test_unknown_event_type_requires_rebuild(self):
        con=sqlite3.connect(self.journal); con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)", ("evt-unknown","rec-001","record_reclassified","2026-08-06T00:00:00+00:00","fixture","{}")); con.commit()
        self.assertEqual(self.projector.run_once().status,ProjectionStatus.REBUILD_REQUIRED)
    def test_orphan_detection_requires_rebuild(self):
        self._full(); con=sqlite3.connect(self.retrieval); con.execute("PRAGMA foreign_keys=OFF"); con.execute("INSERT INTO retrieval_embeddings VALUES ('orphan','x','x',4,x'00000000000000000000000000000000',1,'x','now')"); con.commit(); self.assertEqual(self.projector.validate()["status"],ProjectionStatus.REBUILD_REQUIRED.value)
    def test_journal_is_byte_identical(self):
        before=sha256(self.journal); self._full(); self.assertEqual(sha256(self.journal),before)
    def test_plan_is_read_only(self):
        before = self.retrieval.exists() and sha256(self.retrieval); report=self.projector.plan(); self.assertTrue(report.details["write_free"]); self.assertEqual(before, self.retrieval.exists() and sha256(self.retrieval))
    def test_schema_audit_is_machine_readable(self):
        audit=JournalReader(self.journal).audit(); self.assertEqual(audit["event_count"],55); self.assertIn("memory_events",audit["tables"])
    def test_full_projection_hashes_match_baseline(self):
        self._full(); fresh=sqlite3.connect(self.retrieval); baseline=sqlite3.connect(ROOT/"sqlite-spike/retrieval.db")
        self.assertEqual(fresh.execute("SELECT record_id,projection_hash FROM retrieval_documents ORDER BY record_id").fetchall(),baseline.execute("SELECT record_id,projection_hash FROM retrieval_documents ORDER BY record_id").fetchall())

    def test_verified_inactive_relation_is_excluded_from_current(self):
        self._full(); con=sqlite3.connect(self.retrieval)
        self.assertIsNone(con.execute("SELECT record_id FROM retrieval_documents WHERE record_id='rec-048'").fetchone())
        self.assertEqual(con.execute("SELECT count(*) FROM retrieval_documents WHERE is_current=1").fetchone()[0],50)
        self.assertEqual(self._counts(),(51,51))

    def test_unknown_validation_requires_rebuild_with_link_ids(self):
        con=sqlite3.connect(self.journal)
        con.execute("DELETE FROM artifact_validation_events WHERE artifact_record_id='rec-004'")
        av.rebuild_state(con); con.commit(); con.close()
        report=self.projector.run_once(100)
        self.assertEqual(report.status,ProjectionStatus.REBUILD_REQUIRED)
        self.assertEqual(report.details["issues"],["artifact_validation_missing"])
        self.assertEqual(report.details["record_ids"],["rec-004"])
        self.assertEqual(report.details["artifact_link_ids"],["artifact:rec-004:touches:repo://ai-pos/README.md"])
        self.assertEqual(report.cursor_after,report.cursor_before)

    def test_eligibility_ignores_filesystem_reachability(self):
        # rec-048's target never existed anywhere, yet an explicit human approval
        # makes it projectable; rec-004's target is a real tracked file, yet a
        # missing judgement blocks projection. Reachability decides nothing.
        con=sqlite3.connect(self.journal)
        con.execute("DELETE FROM artifact_validation_events WHERE artifact_record_id='rec-048'")
        add_validation_event(con,"rec-048","repo://ai-pos/missing-file.ts","touches","verified_active","manual_verified",note="explicit human override for test")
        av.rebuild_state(con); con.commit(); con.close()
        self._full(); con=sqlite3.connect(self.retrieval)
        self.assertIsNotNone(con.execute("SELECT record_id FROM retrieval_documents WHERE record_id='rec-048'").fetchone())
        self.assertEqual(self._counts(),(52,52))

    def test_validation_effective_time_is_respected(self):
        con=sqlite3.connect(self.journal)
        add_validation_event(con,"rec-004","repo://ai-pos/README.md","touches","verified_inactive","superseded",effective_at="2099-01-01T00:00:00+00:00",key_suffix=":future")
        av.rebuild_state(con); con.commit(); con.close()
        reader=JournalReader(self.journal)
        lid="artifact:rec-004:touches:repo://ai-pos/README.md"
        self.assertEqual(reader.snapshot("rec-004",validation_as_of="2026-07-11T00:00:00+00:00")["artifact_validation"][lid]["state"],"verified_active")
        self.assertEqual(reader.snapshot("rec-004",validation_as_of="2099-01-02T00:00:00+00:00")["artifact_validation"][lid]["state"],"verified_inactive")
        self._full()  # build time precedes the future inactive event
        con=sqlite3.connect(self.retrieval)
        self.assertIsNotNone(con.execute("SELECT record_id FROM retrieval_documents WHERE record_id='rec-004'").fetchone())

    def test_projector_reads_validation_only_from_journal(self):
        # A journal without the validation tables leaves every relation unknown:
        # the projector stops instead of guessing from any other source.
        journal=Path(self.tmp.name)/"no-validation.db"; journal_fixture(journal,with_validation=False)
        projector=ProjectionProjector(ProjectorConfig(journal,Path(self.tmp.name)/"no-validation-retrieval.db","fake-fingerprint",4,"fake"),FakeEmbedder())
        report=projector.run_once(100)
        self.assertEqual(report.status,ProjectionStatus.REBUILD_REQUIRED)
        self.assertEqual(len(report.details["artifact_link_ids"]),14)

    def test_historical_and_contract_baselines_unchanged(self):
        self.assertEqual(sha256(ROOT/"sqlite-spike/results/vector-baseline.json"),"5caa94f8b6d2ebc82571e290d574cf267016ac03997ab0e374bb9cc2687b68b6")
        self.assertEqual(sha256(ROOT/"sqlite-spike/results/vector-contract-baseline-v1.json"),"fa2e009d10b5d76cf8dff7d6d53217b36bac4b34efae9a9c213a52228b9d60fc")

    def test_public_schema_snapshots_unchanged(self):
        from brain import schemas
        self.assertTrue(schemas.check_exported())

if __name__ == "__main__": unittest.main()
