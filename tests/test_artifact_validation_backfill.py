import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from brain import artifact_validation as av
from journal_fixture import journal_fixture


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

MANIFEST = ROOT / "sqlite-spike/results/artifact-validation-approved.json"
BACKFILL = ROOT / "scripts/backfill_artifact_validation.py"
MIGRATE = ROOT / "scripts/apply_artifact_validation_migration.py"


def run(script, *args):
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.journal = self.root / "journal.db"
        journal_fixture(self.journal, with_validation=False)

    def tearDown(self):
        self.tmp.cleanup()

    def _migrate(self):
        result = run(MIGRATE, "--journal-db", str(self.journal), "--apply", "--backup", str(self.root / "backup.db"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def _manifest_copy(self, mutate=None):
        manifest = json.loads(MANIFEST.read_text())
        if mutate:
            mutate(manifest)
        path = self.root / "manifest.json"
        path.write_text(json.dumps(manifest))
        return path

    def test_migration_script_backup_and_rerun(self):
        result = run(MIGRATE, "--journal-db", str(self.journal), "--apply", "--backup", str(self.root / "backup.db"), "--output", str(self.root / "report.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.root / "report.json").read_text())
        self.assertTrue(report["canonical_tables_unchanged"])
        self.assertEqual(report["backup"]["integrity_check"], "ok")
        self.assertTrue((self.root / "backup.db").is_file())
        rerun = run(MIGRATE, "--journal-db", str(self.journal), "--apply", "--backup", str(self.root / "backup2.db"))
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertTrue(json.loads(rerun.stdout)["migration"]["already_applied"])

    def test_migration_apply_requires_backup(self):
        result = run(MIGRATE, "--journal-db", str(self.journal), "--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --backup", result.stderr)

    def test_backfill_refuses_without_migration(self):
        result = run(BACKFILL, "--journal-db", str(self.journal), "--manifest", str(MANIFEST))
        self.assertEqual(result.returncode, 2)
        self.assertIn("tables missing", result.stdout)

    def test_backfill_refuses_incomplete_manifest(self):
        self._migrate()
        path = self._manifest_copy(lambda m: m["decisions"].pop())
        result = run(BACKFILL, "--journal-db", str(self.journal), "--manifest", str(path))
        self.assertEqual(result.returncode, 2)
        self.assertIn("manifest incomplete", result.stdout)

    def test_backfill_refuses_duplicates_and_invalid_values(self):
        self._migrate()
        path = self._manifest_copy(lambda m: m["decisions"].append(dict(m["decisions"][0])))
        result = run(BACKFILL, "--journal-db", str(self.journal), "--manifest", str(path))
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate decision", result.stdout)

        def bad_state(m):
            m["decisions"][0]["state"] = "active"
        result = run(BACKFILL, "--journal-db", str(self.journal), "--manifest", str(self._manifest_copy(bad_state)))
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid state", result.stdout)

    def test_backfill_refuses_unknown_relation(self):
        self._migrate()

        def unknown(m):
            m["decisions"][0]["artifact_link_id"] = "artifact:rec-999:touches:repo://ai-pos/README.md"
            m["decisions"][0]["artifact_record_id"] = "rec-999"
        result = run(BACKFILL, "--journal-db", str(self.journal), "--manifest", str(self._manifest_copy(unknown)))
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown relation", result.stdout)

    def test_dry_run_writes_nothing(self):
        self._migrate()
        before = sha256(self.journal)
        result = run(BACKFILL, "--journal-db", str(self.journal), "--manifest", str(MANIFEST))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["planned_insert_count"], 14)
        self.assertEqual(sha256(self.journal), before)

    def test_apply_then_idempotent_reapply(self):
        self._migrate()
        con = sqlite3.connect(self.journal)
        digest_before = av.canonical_table_digest(con)
        events_before = con.execute("SELECT count(*) FROM memory_events").fetchone()[0]
        con.close()

        first = run(BACKFILL, "--journal-db", str(self.journal), "--manifest", str(MANIFEST), "--apply", "--output", str(self.root / "first.json"))
        self.assertEqual(first.returncode, 0, first.stderr)
        report = json.loads((self.root / "first.json").read_text())
        self.assertEqual(report["inserted_events"], 14)
        self.assertFalse(report["idempotent_noop"])
        self.assertEqual(report["state_counts"], {"verified_active": 13, "verified_inactive": 1})
        self.assertEqual(report["folded_states"]["artifact:rec-048:touches:repo://ai-pos/missing-file.ts"], "verified_inactive")
        self.assertTrue(report["canonical_tables_unchanged"])
        self.assertEqual(report["fold_mismatches"], [])

        second = run(BACKFILL, "--journal-db", str(self.journal), "--manifest", str(MANIFEST), "--apply", "--output", str(self.root / "second.json"))
        self.assertEqual(second.returncode, 0, second.stderr)
        rerun = json.loads((self.root / "second.json").read_text())
        self.assertEqual(rerun["inserted_events"], 0)
        self.assertTrue(rerun["idempotent_noop"])

        con = sqlite3.connect(self.journal)
        self.assertEqual(av.canonical_table_digest(con), digest_before)
        self.assertEqual(con.execute("SELECT count(*) FROM memory_events").fetchone()[0], events_before)
        self.assertEqual(con.execute("SELECT count(*) FROM artifact_validation_events").fetchone()[0], 14)
        con.close()

    def test_manifest_matches_goal_approval(self):
        manifest = json.loads(MANIFEST.read_text())
        self.assertEqual(manifest["approval"]["approved_by"], "Pavol")
        self.assertEqual(manifest["approval"]["effective_at"], "2026-07-12T00:00:00+02:00")
        self.assertEqual(len(manifest["decisions"]), 14)
        by_id = {d["artifact_record_id"]: d for d in manifest["decisions"]}
        self.assertEqual(by_id["rec-048"]["state"], "verified_inactive")
        self.assertEqual(by_id["rec-048"]["reason_code"], "wrong_target")
        active = [d for d in manifest["decisions"] if d["state"] == "verified_active"]
        self.assertEqual(len(active), 13)
        self.assertTrue(all(d["reason_code"] == "manual_verified" for d in active))


if __name__ == "__main__":
    unittest.main()
