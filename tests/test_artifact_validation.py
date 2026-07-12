import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from brain import artifact_validation as av
from journal_fixture import journal_fixture, add_validation_event


class ArtifactValidationModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Path(self.tmp.name) / "journal.db"
        journal_fixture(self.journal, with_validation=False)
        self.con = sqlite3.connect(self.journal)
        self.con.row_factory = sqlite3.Row

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_migration_on_clean_journal(self):
        self.assertEqual(av.tables_present(self.con), {t: False for t in av.TABLES})
        result = av.apply_migration(self.con)
        self.assertFalse(result["already_applied"])
        self.assertEqual(av.tables_present(self.con), {t: True for t in av.TABLES})
        self.assertEqual(av.rebuild_state(self.con), 0)
        self.assertEqual(av.verify_state(self.con), [])

    def test_migration_rerun_detects_already_applied(self):
        av.apply_migration(self.con)
        digest = av.canonical_table_digest(self.con)
        result = av.apply_migration(self.con)
        self.assertTrue(result["already_applied"])
        self.assertEqual(av.canonical_table_digest(self.con), digest)

    def test_migration_is_additive_only(self):
        digest = av.canonical_table_digest(self.con)
        counts = {t: self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in av.CANONICAL_TABLES}
        av.apply_migration(self.con)
        self.assertEqual(av.canonical_table_digest(self.con), digest)
        self.assertEqual({t: self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in av.CANONICAL_TABLES}, counts)

    def test_folded_state_is_reproduced_from_events(self):
        av.apply_migration(self.con)
        add_validation_event(self.con, "rec-004", "repo://ai-pos/README.md", "touches", "verified_active", "manual_verified")
        add_validation_event(self.con, "rec-048", "repo://ai-pos/missing-file.ts", "touches", "verified_inactive", "wrong_target")
        self.assertEqual(av.rebuild_state(self.con), 2)
        self.assertEqual(av.verify_state(self.con), [])
        states = {row["artifact_link_id"]: row["current_state"] for row in self.con.execute("SELECT * FROM artifact_validation_state")}
        self.assertEqual(states["artifact:rec-004:touches:repo://ai-pos/README.md"], "verified_active")
        self.assertEqual(states["artifact:rec-048:touches:repo://ai-pos/missing-file.ts"], "verified_inactive")

    def test_tampered_state_table_is_detected(self):
        av.apply_migration(self.con)
        add_validation_event(self.con, "rec-004", "repo://ai-pos/README.md", "touches", "verified_active", "manual_verified")
        av.rebuild_state(self.con)
        self.con.execute("UPDATE artifact_validation_state SET current_state='verified_inactive'")
        mismatches = av.verify_state(self.con)
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["artifact_link_id"], "artifact:rec-004:touches:repo://ai-pos/README.md")

    def test_fold_effective_time_semantics(self):
        av.apply_migration(self.con)
        lid = add_validation_event(self.con, "rec-004", "repo://ai-pos/README.md", "touches", "verified_active", "manual_verified", effective_at="2026-07-10T00:00:00+00:00")
        add_validation_event(self.con, "rec-004", "repo://ai-pos/README.md", "touches", "verified_inactive", "superseded", effective_at="2026-07-12T00:00:00+00:00")
        events = av.read_events(self.con)
        self.assertNotIn(lid, av.fold_events(events, as_of="2026-07-09T00:00:00+00:00"))
        self.assertEqual(av.fold_events(events, as_of="2026-07-11T00:00:00+00:00")[lid]["state"], "verified_active")
        self.assertEqual(av.fold_events(events, as_of="2026-07-12T00:00:00+00:00")[lid]["state"], "verified_inactive")
        self.assertEqual(av.fold_events(events)[lid]["state"], "verified_inactive")

    def test_fold_normalizes_timezone_offsets(self):
        av.apply_migration(self.con)
        lid = add_validation_event(self.con, "rec-004", "repo://ai-pos/README.md", "touches", "verified_active", "manual_verified", effective_at="2026-07-12T00:00:00+02:00")
        events = av.read_events(self.con)
        self.assertEqual(av.fold_events(events, as_of="2026-07-11T22:00:00+00:00")[lid]["state"], "verified_active")
        self.assertNotIn(lid, av.fold_events(events, as_of="2026-07-11T21:59:59+00:00"))

    def test_event_replay_is_idempotent_by_key(self):
        av.apply_migration(self.con)
        add_validation_event(self.con, "rec-004", "repo://ai-pos/README.md", "touches", "verified_active", "manual_verified")
        with self.assertRaises(sqlite3.IntegrityError):
            add_validation_event(self.con, "rec-004", "repo://ai-pos/README.md", "touches", "verified_active", "manual_verified")

    def test_journal_relations_derive_from_payload(self):
        relations = av.journal_relations(self.con)
        self.assertEqual(len(relations), 14)
        self.assertIn("artifact:rec-048:touches:repo://ai-pos/missing-file.ts", relations)
        self.assertEqual(self.con.execute("SELECT count(*) FROM artifact_links").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
