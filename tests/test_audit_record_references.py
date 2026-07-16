import importlib.util
import subprocess
import sqlite3
import sys
from pathlib import Path

from journal_fixture import journal_fixture

ROOT = Path(__file__).resolve().parents[1]

SPEC = importlib.util.spec_from_file_location("audit_record_references", ROOT / "scripts/audit_record_references.py")
audit_record_references = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_record_references)


def _make_brain(tmp_path):
    from brain.api import Brain
    from brain.config import BrainConfig

    class NoopTransport:
        def embed(self, text):
            return [1.0, 0.0, 0.0, 0.0]

    journal = tmp_path / "journal.db"
    journal_fixture(journal, instance_id="personal")
    config = BrainConfig(journal_db_path=journal, retrieval_db_path=tmp_path / "retrieval.db",
                          embedding_dimension=4, endpoint_probe_timeout=.01,
                          client_identity="writer", instance_id="personal")
    return Brain(config, NoopTransport()), journal


def test_audit_reports_typed_link_as_clean(tmp_path):
    b, journal = _make_brain(tmp_path)
    problem = b.record_problem(statement="Agents lose context", impact="Repeated explanation",
                                source_assertion="explicit_user_confirmation", workspace="personal")
    b.record_decision(statement="Use separate Brain instances", rationale="Isolation by construction",
                       verdict="accepted", reason="zero leak",
                       links=[{"target_record_id": problem.record_id, "relation": "addresses"}],
                       source_assertion="explicit_user_confirmation", workspace="personal")

    findings = audit_record_references.audit_journal(journal, "personal")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["origin"] == "typed_link"
    assert finding["relation"] == "addresses"
    assert not finding["dangling"] and not finding["cross_workspace"] and not finding["forbidden_origin"]
    assert not audit_record_references._is_bad(finding)


def test_audit_flags_dangling_cross_workspace_and_forbidden_origin_rows(tmp_path):
    b, journal = _make_brain(tmp_path)
    personal = b.record_problem(statement="Scoped source", impact="test",
                                 source_assertion="explicit_user_confirmation", workspace="personal")
    foreign = b.record_problem(statement="Foreign target", impact="test",
                                source_assertion="explicit_user_confirmation", workspace="ai-pos")
    created = "2026-07-13T00:00:00+00:00"
    con = sqlite3.connect(journal)
    # Simulate pre-Package-2 or corrupt data: an evidence/artifact-origin
    # record:// row (relation="evidence"), a dangling record:// row, and a
    # cross-workspace record:// row -- none of these can be produced by the
    # writer post-Package-2, but the audit script must still catch them if
    # they exist (defense in depth, exactly what this script is for).
    con.execute("INSERT INTO artifact_links VALUES (?,?,?,?,?,?,?)",
                (personal.record_id, "record://" + foreign.record_id, "addresses", 1.0, "corrupt-fixture", created, 1))
    con.execute("INSERT INTO artifact_links VALUES (?,?,?,?,?,?,?)",
                (personal.record_id, "record://rec-does-not-exist", "evidence", 1.0, "corrupt-fixture", created, 1))
    con.commit()
    con.close()

    findings = audit_record_references.audit_journal(journal, "personal")
    by_target = {f["target_record"]: f for f in findings}

    cross = by_target[foreign.record_id]
    assert cross["cross_workspace"] and not cross["dangling"] and not cross["forbidden_origin"]
    assert audit_record_references._is_bad(cross)

    dangling = by_target["rec-does-not-exist"]
    assert dangling["dangling"] and dangling["forbidden_origin"]
    assert audit_record_references._is_bad(dangling)

    assert all(audit_record_references._is_bad(f) for f in findings)


def test_audit_cli_exit_code_clean_vs_flagged(tmp_path):
    b, journal = _make_brain(tmp_path)
    problem = b.record_problem(statement="Agents lose context", impact="Repeated explanation",
                                source_assertion="explicit_user_confirmation", workspace="personal")
    b.record_decision(statement="Use separate Brain instances", rationale="Isolation by construction",
                       verdict="accepted", reason="zero leak",
                       links=[{"target_record_id": problem.record_id, "relation": "addresses"}],
                       source_assertion="explicit_user_confirmation", workspace="personal")

    clean = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_record_references.py"), "--journal", f"personal={journal}"],
        capture_output=True, text=True,
    )
    assert clean.returncode == 0
    assert "no record:// references found" not in clean.stdout  # the one typed link is reported
    assert "0 flagged" in clean.stdout

    con = sqlite3.connect(journal)
    con.execute("INSERT INTO artifact_links VALUES (?,?,?,?,?,?,?)",
                (problem.record_id, "record://rec-does-not-exist", "evidence", 1.0, "corrupt-fixture",
                 "2026-07-13T00:00:00+00:00", 1))
    con.commit()
    con.close()

    flagged = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_record_references.py"), "--journal", f"personal={journal}"],
        capture_output=True, text=True,
    )
    assert flagged.returncode == 1
    assert "1 flagged" in flagged.stdout


def test_audit_cli_missing_journal_exits_2(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_record_references.py"), "--journal",
         f"personal={tmp_path / 'does-not-exist.db'}"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2


def test_audit_never_writes_to_the_journal_it_reads(tmp_path):
    b, journal = _make_brain(tmp_path)
    problem = b.record_problem(statement="Agents lose context", impact="Repeated explanation",
                                source_assertion="explicit_user_confirmation", workspace="personal")
    before = journal.read_bytes()
    audit_record_references.audit_journal(journal, "personal")
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/audit_record_references.py"), "--journal", f"personal={journal}"],
        capture_output=True, text=True, check=True,
    )
    assert journal.read_bytes() == before
