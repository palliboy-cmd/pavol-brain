import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from journal_fixture import journal_fixture

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location("stamp_brain_instance", ROOT / "scripts/stamp_brain_instance.py")
stamp = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(stamp)

BOOTSTRAP_SPEC = importlib.util.spec_from_file_location("bootstrap_brain_instances", ROOT / "scripts/bootstrap_brain_instances.py")
bootstrap = importlib.util.module_from_spec(BOOTSTRAP_SPEC); BOOTSTRAP_SPEC.loader.exec_module(bootstrap)

from brain import instance_identity

PERSONAL_WORKSPACES = {"abap-object-exporter", "ai-pos", "ai-pos-app", "personal", "smart-timesheet"}


def personal_journal(path):
    journal_fixture(path)
    built = bootstrap.build(path, str(path) + ".staged", PERSONAL_WORKSPACES, instance_id="personal", source_digest="legacy-digest")
    import os
    os.replace(str(path) + ".staged", path)
    return built


def strip_marker(path):
    con = sqlite3.connect(path)
    con.execute("DROP TABLE brain_instance_identity")
    con.commit(); con.close()


def argv(journal, instance_id, apply=True, source_digest=None, backup_path=None):
    values = ["stamp", "--journal-db", str(journal), "--instance-id", instance_id]
    if source_digest: values += ["--source-digest", source_digest]
    if backup_path: values += ["--backup-path", str(backup_path)]
    if apply: values.append("--apply")
    return values


def run(monkeypatch, capsys, args):
    monkeypatch.setattr(sys, "argv", args)
    code = stamp.main()
    return code, json.loads(capsys.readouterr().out)


def test_missing_journal_is_blocked_without_side_effects(tmp_path, monkeypatch, capsys):
    code, report = run(monkeypatch, capsys, argv(tmp_path / "nope.db", "personal"))
    assert code == 2 and report["blocked"] == "journal_missing"


def test_dry_run_never_mutates_the_journal(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "personal.db"; personal_journal(journal); strip_marker(journal)
    before = journal.read_bytes()
    code, report = run(monkeypatch, capsys, argv(journal, "personal", apply=False))
    assert code == 0 and report["dry_run"] and not report["blocked"]
    assert journal.read_bytes() == before
    assert instance_identity.read_journal_marker(sqlite3.connect(journal)) is None


def test_apply_stamps_and_backs_up_first(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "personal.db"; personal_journal(journal); strip_marker(journal)
    con = sqlite3.connect(journal); con.execute("PRAGMA foreign_keys=OFF")
    canonical_before = bootstrap.logical_digest(con, stamp.CANONICAL_TABLES); con.close()
    code, report = run(monkeypatch, capsys, argv(journal, "personal"))
    assert code == 0 and report["stamped"]
    backup_path = Path(report["backup_path"]); assert backup_path.exists()
    marker = instance_identity.read_journal_marker(sqlite3.connect(journal))
    assert marker["instance_id"] == "personal" and marker["source_digest"] == report["source_digest_to_stamp"]
    con = sqlite3.connect(journal); con.execute("PRAGMA foreign_keys=OFF")
    canonical_after = bootstrap.logical_digest(con, stamp.CANONICAL_TABLES); con.close()
    assert canonical_after == canonical_before, "stamping must never change canonical table content"


def test_explicit_source_digest_is_recorded_instead_of_self_reference(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "personal.db"; personal_journal(journal); strip_marker(journal)
    code, report = run(monkeypatch, capsys, argv(journal, "personal", source_digest="original-manifest-digest"))
    assert code == 0
    marker = instance_identity.read_journal_marker(sqlite3.connect(journal))
    assert marker["source_digest"] == "original-manifest-digest"


def test_rerun_after_success_is_idempotent_and_writes_nothing(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "personal.db"; personal_journal(journal); strip_marker(journal)
    run(monkeypatch, capsys, argv(journal, "personal"))
    before = journal.read_bytes()
    code, report = run(monkeypatch, capsys, argv(journal, "personal"))
    assert code == 0 and report["already_stamped"]
    assert journal.read_bytes() == before


def test_wrong_instance_is_refused_and_marker_unchanged(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "personal.db"; personal_journal(journal); strip_marker(journal)
    run(monkeypatch, capsys, argv(journal, "personal"))
    marker_before = instance_identity.read_journal_marker(sqlite3.connect(journal))
    code, report = run(monkeypatch, capsys, argv(journal, "work"))
    assert code == 2 and report["blocked"] == "marker_mismatch"
    assert instance_identity.read_journal_marker(sqlite3.connect(journal)) == marker_before


def test_workspace_partition_violation_is_refused_without_guessing(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "mixed.db"; journal_fixture(journal)  # fixture spans both personal and sap-work
    before = journal.read_bytes()
    code, report = run(monkeypatch, capsys, argv(journal, "personal"))
    assert code == 2 and report["blocked"] == "workspace_partition_violation"
    assert report["foreign_workspaces"] == ["sap-work"]
    assert journal.read_bytes() == before
    assert instance_identity.read_journal_marker(sqlite3.connect(journal)) is None


def test_corrupt_journal_is_refused(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "garbage.db"; journal.write_text("not a database")
    code, report = run(monkeypatch, capsys, argv(journal, "personal"))
    assert code == 2 and report["blocked"] == "integrity_check_failed"


def test_backup_restored_on_failure_mid_apply(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "personal.db"; personal_journal(journal); strip_marker(journal)
    con = sqlite3.connect(journal); con.execute("PRAGMA foreign_keys=OFF")
    digest_before = bootstrap.logical_digest(con, stamp.CANONICAL_TABLES); con.close()
    def broken_stamp(*a, **k): raise RuntimeError("simulated failure after backup")
    monkeypatch.setattr(stamp, "apply_stamp", broken_stamp)
    monkeypatch.setattr(sys, "argv", argv(journal, "personal"))
    with pytest.raises(RuntimeError, match="simulated failure"):
        stamp.main()
    # sqlite3.Connection.backup() is a logical, not byte-for-byte, copy (page
    # layout/freelist bookkeeping can differ) — the restore is verified by
    # canonical content digest and marker absence, not raw file bytes.
    con = sqlite3.connect(journal); con.execute("PRAGMA foreign_keys=OFF")
    digest_after = bootstrap.logical_digest(con, stamp.CANONICAL_TABLES); con.close()
    assert digest_after == digest_before
    assert instance_identity.read_journal_marker(sqlite3.connect(journal)) is None
