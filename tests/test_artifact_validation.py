import json
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from brain import artifact_validation as av
from brain.artifact_verifier import verify
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


class ArtifactTrustViewTests(unittest.TestCase):
    """B9/Package 6 (§8): the read-only response-facing trust object.

    ``trust_view`` never has DB access of its own -- it only folds a row
    (or its absence) into the client-facing shape -- so these are pure unit
    tests, independent of the write path exercised in test_brain_write.py.
    """

    def test_missing_validation_state_fails_safe_to_unverified_reference(self):
        self.assertEqual(av.trust_view(None), {
            "state": "unverified_reference", "method": None, "verifier": None,
            "verified_at": None, "digest": None, "reason": None,
        })

    def test_verified_active_state_surfaces_persisted_verifier_metadata(self):
        row = {
            "current_state": "verified_active",
            "evidence": '{"method":"git_ls_files","verifier":"server-artifact-validator",'
                        '"verifier_instance":"personal","verified_at":"2026-07-16T00:00:00+00:00",'
                        '"object_digest":"deadbeef","repo_alias":"pavol-brain","reason":null}',
        }
        self.assertEqual(av.trust_view(row), {
            "state": "verified_active", "method": "git_ls_files", "verifier": "server-artifact-validator",
            "verified_at": "2026-07-16T00:00:00+00:00", "digest": "deadbeef", "reason": None,
        })

    def test_unknown_state_surfaces_as_unverified_reference_with_reason(self):
        row = {
            "current_state": "unknown",
            "evidence": '{"method":"not_deterministically_verifiable","verifier":"server-artifact-validator",'
                        '"verifier_instance":"personal","verified_at":"2026-07-16T00:00:00+00:00",'
                        '"object_digest":null,"repo_alias":null,"reason":"not_deterministically_verifiable"}',
        }
        trust = av.trust_view(row)
        self.assertEqual(trust["state"], "unverified_reference")
        self.assertEqual(trust["reason"], "not_deterministically_verifiable")
        self.assertIsNone(trust["digest"])

    def test_verified_inactive_state_carries_no_unknown_reason(self):
        row = {
            "current_state": "verified_inactive",
            "evidence": '{"method":"git_ls_files","verifier":"server-artifact-validator",'
                        '"verifier_instance":"personal","verified_at":"2026-07-16T00:00:00+00:00",'
                        '"object_digest":null,"repo_alias":"pavol-brain","reason":null}',
        }
        trust = av.trust_view(row)
        self.assertEqual(trust["state"], "verified_inactive")
        self.assertIsNone(trust["reason"])


class ArtifactVerifierDigestTests(unittest.TestCase):
    """B9/Package 6 (§8, §5 digest semantics): a verifiable repo:// or git://
    target records a cheaply-available stable Git object digest alongside
    the existence verdict; everything else stays null rather than a guess.
    Uses this checkout's own repo as the fixture (mirrors test_brain_write.py's
    ``outcome()`` default, which already relies on the ``pavol-brain`` alias
    resolving to this repository)."""

    def test_repo_uri_digest_is_cheap_git_blob_sha(self):
        result = verify("repo://pavol-brain/brain/api.py", {"pavol-brain": str(ROOT)})
        self.assertEqual(result["state"], "verified_active")
        self.assertEqual(result["repo_alias"], "pavol-brain")
        self.assertRegex(result["object_digest"] or "", r"^[0-9a-f]{40}$")

    def test_git_commit_uri_digest_is_the_resolved_commit_sha(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        result = verify(f"git://pavol-brain/commit/{head}", {"pavol-brain": str(ROOT)})
        self.assertEqual(result["state"], "verified_active")
        self.assertEqual(result["object_digest"], head)

    def test_verified_inactive_targets_have_no_digest(self):
        missing = verify("repo://pavol-brain/does-not-exist", {"pavol-brain": str(ROOT)})
        self.assertEqual(missing["state"], "verified_inactive")
        self.assertIsNone(missing["object_digest"])

    def test_unverifiable_scheme_has_no_digest_or_alias(self):
        result = verify("doc://synthetic/x", {})
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["method"], "not_deterministically_verifiable")
        self.assertIsNone(result["object_digest"])
        self.assertIsNone(result["repo_alias"])

    def test_unknown_repo_alias_records_alias_without_digest(self):
        result = verify("repo://ghost-repo/some/file.py", {})
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(result["method"], "repo_unavailable")
        self.assertEqual(result["repo_alias"], "ghost-repo")
        self.assertIsNone(result["object_digest"])


class ArtifactVerifierArgumentIsolationTests(unittest.TestCase):
    """B9 repair (F1, docs/reviews/package-6-artifact-trust-review.md): the
    relative-path/revision component of a repo:// or git:// URI is client
    controlled. Before the fix, ``git ls-files --error-unmatch <relative>``
    and ``git cat-file -e <revision>^{commit}`` had no options terminator,
    so a component beginning with ``-`` (e.g. ``-v``) was consumed as a git
    option instead of a pathspec/object, the check exited 0 with nothing to
    fail on, and a nonexistent artifact minted ``verified_active``."""

    OPTION_LIKE_RELATIVE = ("-v", "--", "--error-unmatch", "-n", "-c", "--exclude-standard")
    OPTION_LIKE_REVISION = ("-v", "--help", "--", "-n")

    def test_option_like_relative_path_never_verifies_active(self):
        for relative in self.OPTION_LIKE_RELATIVE:
            with self.subTest(relative=relative):
                result = verify(f"repo://pavol-brain/{relative}", {"pavol-brain": str(ROOT)})
                self.assertNotEqual(result["state"], "verified_active")
                self.assertFalse(result["valid"])
                self.assertIsNone(result["object_digest"])

    def test_option_like_git_revision_never_verifies_active(self):
        for revision in self.OPTION_LIKE_REVISION:
            with self.subTest(revision=revision):
                result = verify(f"git://pavol-brain/commit/{revision}", {"pavol-brain": str(ROOT)})
                self.assertNotEqual(result["state"], "verified_active")
                self.assertFalse(result["valid"])
                self.assertIsNone(result["object_digest"])

    def test_traversal_and_absolute_relative_paths_never_verify_active(self):
        for relative in ("../../etc/passwd", "/etc/passwd", "sub/../../../etc/passwd"):
            with self.subTest(relative=relative):
                result = verify(f"repo://pavol-brain/{relative}", {"pavol-brain": str(ROOT)})
                self.assertNotEqual(result["state"], "verified_active")

    def test_nul_byte_relative_path_and_revision_never_verify_active(self):
        self.assertNotEqual(verify("repo://pavol-brain/a\x00b", {"pavol-brain": str(ROOT)})["state"], "verified_active")
        self.assertNotEqual(verify("git://pavol-brain/commit/a\x00b", {"pavol-brain": str(ROOT)})["state"], "verified_active")

    def test_normal_tracked_file_still_verifies_active(self):
        result = verify("repo://pavol-brain/brain/api.py", {"pavol-brain": str(ROOT)})
        self.assertEqual(result["state"], "verified_active")

    def test_normal_missing_file_stays_verified_inactive(self):
        result = verify("repo://pavol-brain/does-not-exist", {"pavol-brain": str(ROOT)})
        self.assertEqual(result["state"], "verified_inactive")

    def test_valid_commit_sha_still_verifies_active(self):
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        result = verify(f"git://pavol-brain/commit/{head}", {"pavol-brain": str(ROOT)})
        self.assertEqual(result["state"], "verified_active")
        self.assertEqual(result["object_digest"], head)

    def test_invalid_normal_revision_stays_verified_inactive(self):
        result = verify("git://pavol-brain/commit/" + "0" * 40, {"pavol-brain": str(ROOT)})
        self.assertEqual(result["state"], "verified_inactive")
        self.assertIsNone(result["object_digest"])

    def test_legitimate_dash_prefixed_tracked_filename_is_conservatively_rejected(self):
        """The path/git model chosen here does not support verifying a real
        tracked file whose name begins with `-`: any relative component that
        could be misread as an option is rejected before it ever reaches
        git, which is what closes F1. The `--` terminator alone would let
        such a name verify correctly, but rejecting option-shaped input
        outright is the simpler, audit-friendly invariant, and no artifact
        this module is used to verify is expected to have such a name."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
            dashed = repo / "-tracked-file.txt"
            dashed.write_text("x")
            subprocess.run(["git", "add", "--", str(dashed)], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
            result = verify("repo://alias/-tracked-file.txt", {"alias": str(repo)})
            self.assertNotEqual(result["state"], "verified_active")


class ArtifactTrustViewFailSafeTests(unittest.TestCase):
    """B9 repair (F2, docs/reviews/package-6-artifact-trust-review.md):
    ``trust_view``'s ``json.loads(state_row["evidence"])`` had no guard, so
    malformed/legacy evidence raised ``JSONDecodeError`` out of the read
    path (``get_related``/``search(include_artifacts=True)``) instead of
    failing safe like the missing-row case."""

    def test_invalid_json_text_fails_safe(self):
        row = {"current_state": "verified_active", "evidence": "{not json"}
        self.assertEqual(av.trust_view(row), {**av._UNVERIFIED, "reason": "malformed_validation_metadata"})

    def test_empty_string_evidence_keeps_existing_null_metadata_behavior(self):
        # Not malformed JSON -- absent evidence -- which is the pre-existing,
        # already-reviewed "folds cleanly" behavior (state stands, metadata
        # null); F2 does not change this case.
        row = {"current_state": "verified_active", "evidence": ""}
        self.assertEqual(av.trust_view(row), {**av._UNVERIFIED, "state": "verified_active"})

    def test_json_list_instead_of_object_fails_safe(self):
        row = {"current_state": "verified_active", "evidence": "[1,2,3]"}
        self.assertEqual(av.trust_view(row), {**av._UNVERIFIED, "reason": "malformed_validation_metadata"})

    def test_json_scalar_fails_safe(self):
        for evidence in ("42", '"a string"', "true"):
            with self.subTest(evidence=evidence):
                row = {"current_state": "verified_active", "evidence": evidence}
                self.assertEqual(av.trust_view(row), {**av._UNVERIFIED, "reason": "malformed_validation_metadata"})

    def test_json_null_fails_safe(self):
        row = {"current_state": "verified_active", "evidence": "null"}
        self.assertEqual(av.trust_view(row), {**av._UNVERIFIED, "reason": "malformed_validation_metadata"})

    def test_object_with_wrong_field_types_does_not_crash(self):
        row = {"current_state": "verified_active",
               "evidence": json.dumps({"method": 123, "verifier": ["a", "b"], "object_digest": 4.5, "reason": None})}
        trust = av.trust_view(row)
        self.assertEqual(trust["state"], "verified_active")
        self.assertEqual(trust["method"], 123)

    def test_missing_evidence_field_does_not_raise_key_error(self):
        row = {"current_state": "verified_active"}
        self.assertEqual(av.trust_view(row), {**av._UNVERIFIED, "state": "verified_active"})

    def test_verified_active_state_with_malformed_metadata_never_reports_verified(self):
        # The exact failure scenario in the review: the fold state itself
        # says verified_active, but the joined event's evidence is corrupt.
        # The response must never claim verified.
        row = {"current_state": "verified_active", "evidence": "{broken"}
        trust = av.trust_view(row)
        self.assertEqual(trust["state"], "unverified_reference")
        self.assertNotIn("verified_active", trust.values())


if __name__ == "__main__":
    unittest.main()
