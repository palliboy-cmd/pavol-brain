import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from journal_fixture import journal_fixture
from brain.errors import BrainError
from brain import instance_identity

pytestmark = pytest.mark.acceptance  # §10 rows 1-9b, 15 (save-gate part), 28 -- see tests/ACCEPTANCE_MATRIX.md

ROOT=Path(__file__).parents[1]
SPEC=importlib.util.spec_from_file_location("bootstrap_brain_instances",ROOT/"scripts/bootstrap_brain_instances.py")
bootstrap=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(bootstrap)
PARTITION=["--personal-workspaces","abap-object-exporter,ai-pos,ai-pos-app,personal,smart-timesheet","--work-workspaces","sap-work"]


def source_digest(path):
    con=bootstrap.readonly(path)
    try:return bootstrap.logical_digest(con,bootstrap.TABLES)
    finally:con.close()


def approved_exclusion(path, manifest, **overrides):
    entry={
        "record_id":"rec-056",
        "workspace":"sap-work",
        "action":"exclude_from_work_split",
        "reason":"Legacy synthetic artifact relation has no confirmed WORK ownership; source context belongs to Personal.",
        "approval":{"approved_by":"Pavol","approved_at":"2026-07-13T12:00:00+02:00","approval_ref":"M1-live-split"},
        "expected_reference":{"field_path":"payload.source_record","target_record_id":"rec-001"},
    }
    entry.update(overrides.pop("entry",{}))
    data={"schema_version":1,"expected_source_logical_digest":source_digest(path),"exclusions":[entry]}
    data.update(overrides)
    manifest.write_text(json.dumps(data))
    return manifest


def argv(source,personal,work,manifest,exclusion=None,apply=True):
    values=["bootstrap","--source",str(source),"--personal-journal",str(personal),"--work-journal",str(work),*PARTITION,"--manifest",str(manifest)]
    if exclusion:values.extend(["--exclusion-manifest",str(exclusion)])
    if apply:values.append("--apply")
    return values


def test_cross_payload_reference_rec_056_is_a_blocking_audit_finding(tmp_path):
    source=tmp_path/"legacy.db";journal_fixture(source);manifest=tmp_path/"manifest.json"
    command=[sys.executable,str(ROOT/"scripts/bootstrap_brain_instances.py"),"--source",str(source),
             "--personal-journal",str(tmp_path/"personal.db"),"--work-journal",str(tmp_path/"work.db"),*PARTITION,
             "--manifest",str(manifest),"--apply"]
    result=subprocess.run(command,capture_output=True,text=True)
    assert result.returncode==2
    rows=json.loads(manifest.read_text())["operator_audit"]["cross_partition_references"]
    finding=next(row for row in rows if row["source_record"]=="rec-056" and row["target_record"]=="rec-001")
    assert finding["field_path"]=="payload.source_record"
    assert finding["source_workspace"]=="sap-work" and finding["target_workspace"]=="ai-pos"
    assert not (tmp_path/"personal.db").exists() and not (tmp_path/"work.db").exists()


def test_approved_exclusion_preserves_source_publishes_pair_and_leaves_no_cross_instance_reference(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json");before=source.read_bytes()
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion,apply=False))
    bootstrap.main();dry_run=json.loads(manifest.read_text())
    assert dry_run["expected_partition_counts"]["personal"]["memory_records"]==51
    assert dry_run["expected_partition_counts"]["work"]["memory_records"]==3
    assert dry_run["expected_partition_counts"]["approved_exclusions"]["memory_records"]==1
    assert dry_run["expected_partition_counts"]["personal"]["memory_events"]==51
    assert dry_run["expected_partition_counts"]["work"]["memory_events"]==3
    assert dry_run["expected_partition_counts"]["approved_exclusions"]["memory_events"]==1
    assert not dry_run["operator_audit"]["partition_count_mismatches"]
    assert not personal.exists() and not work.exists() and source.read_bytes()==before
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main();report=json.loads(manifest.read_text())
    assert source.read_bytes()==before and report["source_sha256_before"]==report["source_sha256_after"]
    assert report["personal"]["counts"]["memory_records"]==51 and report["work"]["counts"]["memory_records"]==3
    assert report["expected_partition_counts"]["approved_exclusions"]["memory_records"]==1
    assert not report["operator_audit"]["partition_count_mismatches"]
    assert report["operator_audit"]["approved_exclusions"]["legacy_retention"]["records_present"]==["rec-056"]
    assert report["operator_audit"]["approved_exclusions"]["events"]
    assert report["operator_audit"]["approved_exclusions"]["legacy_retention"]["events_present"]==[
        event["event_id"] for event in report["operator_audit"]["approved_exclusions"]["events"]
    ]
    assert report["operator_audit"]["approved_exclusions"]["legacy_retention"]["artifact_validation_events_present"]==[
        event["event_id"] for event in report["operator_audit"]["approved_exclusions"]["artifact_validation_events"]
    ]
    assert report["operator_audit"]["approved_exclusions"]["remaining_cross_partition_references"]==[]
    assert "rec-001" not in json.dumps(report["work"])
    assert report["published"] and report["result_journal_digests"]
    assert sqlite3.connect(personal).execute("SELECT count(*) FROM memory_records WHERE workspace='sap-work'").fetchone()[0]==0
    assert sqlite3.connect(work).execute("SELECT count(*) FROM memory_records WHERE workspace!='sap-work'").fetchone()[0]==0
    work_con=sqlite3.connect(work)
    assert bootstrap.reference_audit(work_con,set(),{"sap-work"})==[]
    assert work_con.execute("SELECT count(*) FROM memory_records WHERE record_id IN ('rec-056','rec-001')").fetchone()[0]==0


def test_exclusion_manifest_rejects_changed_digest(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";journal_fixture(source);manifest=tmp_path/"manifest.json";exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    data=json.loads(exclusion.read_text());data["expected_source_logical_digest"]="0"*64;exclusion.write_text(json.dumps(data))
    monkeypatch.setattr(sys,"argv",argv(source,tmp_path/"personal.db",tmp_path/"work.db",manifest,exclusion))
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code==2 and "does not match snapshot" in json.loads(manifest.read_text())["operator_audit"]["exclusion_manifest_error"]


def test_exclusion_manifest_rejects_changed_payload_or_reference_path(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";journal_fixture(source);manifest=tmp_path/"manifest.json";exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    con=sqlite3.connect(source);payload=json.loads(con.execute("SELECT payload FROM memory_records WHERE record_id='rec-056'").fetchone()[0])
    payload["source_record"]="rec-053";raw=json.dumps(payload,sort_keys=True,separators=(",",":"));con.execute("UPDATE memory_records SET payload=?,raw_input=? WHERE record_id='rec-056'",(raw,raw));con.commit();con.close()
    approved_exclusion(source,exclusion)
    monkeypatch.setattr(sys,"argv",argv(source,tmp_path/"personal.db",tmp_path/"work.db",manifest,exclusion))
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code==2 and "payload reference does not match snapshot" in json.loads(manifest.read_text())["operator_audit"]["exclusion_manifest_error"]
    source.unlink();journal_fixture(source);approved_exclusion(source,exclusion,entry={"expected_reference":{"field_path":"payload.other","target_record_id":"rec-001"}})
    monkeypatch.setattr(sys,"argv",argv(source,tmp_path/"personal-2.db",tmp_path/"work-2.db",manifest,exclusion))
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code==2 and "unsupported exclusion expected_reference.field_path" in json.loads(manifest.read_text())["operator_audit"]["exclusion_manifest_error"]


@pytest.mark.parametrize("entry",[
    {"record_id":"rec-999"},
    {"record_id":"rec-056", "workspace":"sap-work", "action":"exclude_from_work_split", "reason":"x", "approval":{"approved_by":"Pavol","approved_at":"2026-07-13T12:00:00+02:00","approval_ref":"M1-live-split"}, "expected_reference":{"field_path":"payload.source_record","target_record_id":"rec-001"}},
])
def test_exclusion_manifest_rejects_unknown_or_extra_exclusions(tmp_path,monkeypatch,entry):
    source=tmp_path/"legacy.db";journal_fixture(source);manifest=tmp_path/"manifest.json";exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    data=json.loads(exclusion.read_text())
    if entry["record_id"]=="rec-999":data["exclusions"][0]["record_id"]="rec-999"
    else:data["exclusions"].append(entry)
    exclusion.write_text(json.dumps(data))
    monkeypatch.setattr(sys,"argv",argv(source,tmp_path/"personal.db",tmp_path/"work.db",manifest,exclusion))
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code==2 and "exclusion_manifest_error" in json.loads(manifest.read_text())["operator_audit"]


def test_second_build_failure_cleans_staging_and_retry_succeeds(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    original=bootstrap.build;calls=0
    def fail_second(*args,**kwargs):
        nonlocal calls;calls+=1
        if calls==2:raise RuntimeError("injected second build failure")
        return original(*args,**kwargs)
    monkeypatch.setattr(bootstrap,"build",fail_second);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="second build"):bootstrap.main()
    assert not personal.exists() and not work.exists() and not Path(str(personal)+".staging").exists() and not Path(str(work)+".staging").exists()
    monkeypatch.setattr(bootstrap,"build",original);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main();assert personal.exists() and work.exists()


def test_count_gate_failure_publishes_nothing(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    original=bootstrap.build;calls=0
    def wrong_count(*args,**kwargs):
        nonlocal calls;calls+=1;report=original(*args,**kwargs)
        if calls==2:report["counts"]["memory_events"]-=1
        return report
    monkeypatch.setattr(bootstrap,"build",wrong_count);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="count mismatch"):bootstrap.main()
    assert not personal.exists() and not work.exists()


def test_integrity_or_fk_gate_failure_publishes_nothing(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    monkeypatch.setattr(bootstrap.av,"verify_state",lambda con:[{"injected":"mismatch"}])
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="validation failed"):bootstrap.main()
    assert not personal.exists() and not work.exists()


def test_reported_fk_failure_publishes_nothing(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    original=bootstrap.build;calls=0
    def reported_fk(*args,**kwargs):
        nonlocal calls;calls+=1;report=original(*args,**kwargs)
        if calls==2:report["foreign_key_violations"]=[("injected",)]
        return report
    monkeypatch.setattr(bootstrap,"build",reported_fk);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="work staging validation failed"):bootstrap.main()
    assert not personal.exists() and not work.exists()


def test_second_publish_failure_rolls_back_first_target(tmp_path,monkeypatch):
    staged_personal=tmp_path/"personal.staging";staged_work=tmp_path/"work.staging"
    personal=tmp_path/"personal.db";work=tmp_path/"work.db"
    staged_personal.write_text("personal");staged_work.write_text("work")
    original=bootstrap.os.replace;calls=0
    def fail_second(source,target):
        nonlocal calls;calls+=1
        if calls==2:raise OSError("injected publish failure")
        return original(source,target)
    monkeypatch.setattr(bootstrap.os,"replace",fail_second)
    with pytest.raises(OSError,match="publish failure"):bootstrap.publish_pair(staged_personal,staged_work,personal,work)
    assert not personal.exists() and not work.exists()


# --- Package 8 follow-up: remaining §10 row 1 failure-injection points
# (snapshot, first build, marker write, first os.replace) + row 4 aggregate
# ("never exactly one target") -- see
# docs/reviews/package-8-acceptance-suite-review.md, Known limitation #1.

def test_snapshot_failure_before_staging_leaves_no_targets_and_retry_succeeds(tmp_path,monkeypatch):
    """§10 row 1 -- the one injection point that runs before any staging file
    is created; a failure here must leave the source untouched and nothing
    on disk at either target, and a clean retry must still succeed."""
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    before=source.read_bytes()
    original=bootstrap.snapshot_source
    def fail_snapshot(*args,**kwargs):raise RuntimeError("injected snapshot failure")
    monkeypatch.setattr(bootstrap,"snapshot_source",fail_snapshot);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="injected snapshot failure"):bootstrap.main()
    assert source.read_bytes()==before
    assert not personal.exists() and not work.exists() and not Path(str(personal)+".staging").exists() and not Path(str(work)+".staging").exists()
    assert not manifest.exists() and not manifest.with_name(manifest.name+".publish-pending").exists()
    monkeypatch.setattr(bootstrap,"snapshot_source",original);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main();assert personal.exists() and work.exists()


def test_first_build_failure_cleans_staging_and_retry_succeeds(tmp_path,monkeypatch):
    """§10 row 1 -- the *first* `build()` call (personal) fails, as distinct
    from the already-covered second (`test_second_build_failure_cleans_staging_and_retry_succeeds`)."""
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    before=source.read_bytes()
    original=bootstrap.build;calls=0
    def fail_first(*args,**kwargs):
        nonlocal calls;calls+=1
        if calls==1:raise RuntimeError("injected first build failure")
        return original(*args,**kwargs)
    monkeypatch.setattr(bootstrap,"build",fail_first);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="first build"):bootstrap.main()
    assert source.read_bytes()==before
    assert not personal.exists() and not work.exists() and not Path(str(personal)+".staging").exists() and not Path(str(work)+".staging").exists()
    assert not manifest.exists() and not manifest.with_name(manifest.name+".publish-pending").exists()
    monkeypatch.setattr(bootstrap,"build",original);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main();assert personal.exists() and work.exists()


def test_marker_write_failure_leaves_no_marker_and_no_targets_retry_succeeds(tmp_path,monkeypatch):
    """§10 row 1 -- the marker write itself (between staging and publish)
    fails: nothing has been published yet, so the correct recovery
    classification is FRESH, not a recovery state."""
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    before=source.read_bytes()
    marker=manifest.with_name(manifest.name+".publish-pending")
    original=bootstrap.write_json_atomic
    def fail_on_marker(path,data):
        if Path(path)==marker:raise RuntimeError("injected marker write failure")
        return original(path,data)
    monkeypatch.setattr(bootstrap,"write_json_atomic",fail_on_marker);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="injected marker write failure"):bootstrap.main()
    assert source.read_bytes()==before
    assert not personal.exists() and not work.exists() and not Path(str(personal)+".staging").exists() and not Path(str(work)+".staging").exists()
    assert not marker.exists() and not manifest.exists()
    assert bootstrap.classify_recovery(marker,manifest,personal,work)[0]=="fresh"
    monkeypatch.setattr(bootstrap,"write_json_atomic",original);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main();assert personal.exists() and work.exists() and json.loads(manifest.read_text())["published"]


def test_first_publish_replace_failure_removes_marker_and_staging_retry_succeeds(tmp_path,monkeypatch):
    """§10 row 1 -- the *first* `os.replace` (personal) fails outright,
    before either target is published, as distinct from the already-covered
    second (`test_second_publish_failure_rolls_back_first_target`, which
    exercises `publish_pair` directly rather than the full `main()` retry
    path)."""
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    before=source.read_bytes()
    original_replace=bootstrap.os.replace;calls=0
    def fail_first(src,dst):
        nonlocal calls
        if Path(dst) in (personal,work):
            calls+=1
            if calls==1:raise OSError("injected first publish failure")
        return original_replace(src,dst)
    monkeypatch.setattr(bootstrap.os,"replace",fail_first);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(OSError,match="injected first publish failure"):bootstrap.main()
    assert source.read_bytes()==before
    assert not personal.exists() and not work.exists() and not Path(str(personal)+".staging").exists() and not Path(str(work)+".staging").exists()
    assert not manifest.with_name(manifest.name+".publish-pending").exists() and not manifest.exists()
    monkeypatch.setattr(bootstrap.os,"replace",original_replace);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main();assert personal.exists() and work.exists() and json.loads(manifest.read_text())["published"]


def _t04_snapshot_failure(monkeypatch,personal,work,manifest):
    def fail(*a,**k):raise RuntimeError("injected")
    monkeypatch.setattr(bootstrap,"snapshot_source",fail)


def _t04_first_build_failure(monkeypatch,personal,work,manifest):
    original=bootstrap.build;calls=0
    def fail(*args,**kwargs):
        nonlocal calls;calls+=1
        if calls==1:raise RuntimeError("injected")
        return original(*args,**kwargs)
    monkeypatch.setattr(bootstrap,"build",fail)


def _t04_second_build_failure(monkeypatch,personal,work,manifest):
    original=bootstrap.build;calls=0
    def fail(*args,**kwargs):
        nonlocal calls;calls+=1
        if calls==2:raise RuntimeError("injected")
        return original(*args,**kwargs)
    monkeypatch.setattr(bootstrap,"build",fail)


def _t04_count_mismatch_gate(monkeypatch,personal,work,manifest):
    original=bootstrap.build;calls=0
    def fail(*args,**kwargs):
        nonlocal calls;calls+=1;report=original(*args,**kwargs)
        if calls==2:report["counts"]["memory_events"]-=1
        return report
    monkeypatch.setattr(bootstrap,"build",fail)


def _t04_fk_gate_failure(monkeypatch,personal,work,manifest):
    monkeypatch.setattr(bootstrap.av,"verify_state",lambda con:[{"injected":"mismatch"}])


def _t04_marker_write_failure(monkeypatch,personal,work,manifest):
    marker=manifest.with_name(manifest.name+".publish-pending")
    original=bootstrap.write_json_atomic
    def fail(path,data):
        if Path(path)==marker:raise RuntimeError("injected")
        return original(path,data)
    monkeypatch.setattr(bootstrap,"write_json_atomic",fail)


def _t04_first_replace_failure(monkeypatch,personal,work,manifest):
    original=bootstrap.os.replace;calls=0
    def fail(src,dst):
        nonlocal calls
        if Path(dst) in (personal,work):
            calls+=1
            if calls==1:raise OSError("injected")
        return original(src,dst)
    monkeypatch.setattr(bootstrap.os,"replace",fail)


def _t04_second_replace_failure(monkeypatch,personal,work,manifest):
    original=bootstrap.os.replace;calls=0
    def fail(src,dst):
        nonlocal calls
        if Path(dst) in (personal,work):
            calls+=1
            if calls==2:raise OSError("injected")
        return original(src,dst)
    monkeypatch.setattr(bootstrap.os,"replace",fail)


def _t04_manifest_write_failure(monkeypatch,personal,work,manifest):
    original=bootstrap.write_json_atomic
    def fail(path,data):
        if Path(path)==manifest:raise RuntimeError("injected")
        return original(path,data)
    monkeypatch.setattr(bootstrap,"write_json_atomic",fail)


def _t04_crash_between_replaces_recoverable_partial(monkeypatch,personal,work,manifest):
    original=bootstrap.os.replace;calls=0
    def crash(src,dst):
        nonlocal calls
        if Path(dst) in (personal,work):
            calls+=1
            if calls==2:raise KeyboardInterrupt("hard crash between the two publishes")
        return original(src,dst)
    monkeypatch.setattr(bootstrap.os,"replace",crash)


T04_SCENARIOS=[
    ("snapshot_failure",_t04_snapshot_failure,False),
    ("first_build_failure",_t04_first_build_failure,False),
    ("second_build_failure",_t04_second_build_failure,False),
    ("count_mismatch_gate",_t04_count_mismatch_gate,False),
    ("fk_gate_failure",_t04_fk_gate_failure,False),
    ("marker_write_failure",_t04_marker_write_failure,False),
    ("first_replace_failure",_t04_first_replace_failure,False),
    ("second_replace_failure",_t04_second_replace_failure,False),
    ("manifest_write_failure",_t04_manifest_write_failure,False),
    ("crash_between_replaces_recoverable_partial",_t04_crash_between_replaces_recoverable_partial,True),
]


@pytest.mark.parametrize("name,inject,expect_exactly_one",T04_SCENARIOS,ids=[s[0] for s in T04_SCENARIOS])
def test_T04_every_bootstrap_failure_injection_never_leaves_exactly_one_target_unless_recoverable_partial(tmp_path,monkeypatch,name,inject,expect_exactly_one):
    """§10 row 4: after any row 1-3 failure-injection scenario, either both
    instance journals exist or neither does. The sole deliberate exception is
    a hard crash landing between the two `publish_pair` `os.replace` calls
    (simulated with `KeyboardInterrupt`, which bypasses `publish_pair`'s own
    `except Exception` rollback exactly as an uncatchable process kill
    would) -- §4.3 classifies that as `recoverable_partial` (exactly one
    target published). When the survivor has since diverged from what was
    staged (a legitimate post-publish write, exactly as
    `test_B1_half_published_target_with_post_publish_write_is_never_clobbered_by_retry`
    exercises), a retry must refuse and must never touch it -- fail-closed,
    not silently completed."""
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    before=source.read_bytes()
    inject(monkeypatch,personal,work,manifest)
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(BaseException):
        bootstrap.main()
    monkeypatch.undo()
    assert source.read_bytes()==before

    personal_exists,work_exists=personal.exists(),work.exists()
    if expect_exactly_one:
        assert personal_exists and not work_exists,name
        marker=manifest.with_name(manifest.name+".publish-pending")
        assert bootstrap.classify_recovery(marker,manifest,personal,work)[0]=="recoverable_partial"
        con=sqlite3.connect(personal);con.execute("PRAGMA foreign_keys=OFF")
        con.execute("INSERT INTO memory_records VALUES ('rec-t04-post-publish',2,'problem','personal','normal','{}','{}','deadbeef','idem-t04-post-publish','probe-agent','explicit_user_command',NULL,NULL,NULL,1.0,'2026-07-15T00:00:00+00:00','2026-07-15T00:00:00+00:00')")
        con.execute("INSERT INTO memory_events VALUES ('evt-t04-post-publish','rec-t04-post-publish','record_created','2026-07-15T00:00:00+00:00','probe-agent','{}')")
        con.execute("INSERT INTO record_state VALUES ('rec-t04-post-publish','accepted','auto_accepted',NULL,NULL,NULL,NULL,'none',NULL,NULL,'evt-t04-post-publish')")
        con.commit();con.close()
        before_bytes=personal.read_bytes()
        monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
        with pytest.raises(SystemExit) as error:bootstrap.main()
        assert error.value.code==4
        assert personal.read_bytes()==before_bytes and not work.exists() and not manifest.exists()
        assert marker.exists()
    else:
        assert (personal_exists and work_exists) or (not personal_exists and not work_exists),name


# The two tests previously here (test_retry_recovers_a_crash_marker_and_partial_target,
# test_retry_keeps_a_completed_pair_if_crash_happened_after_manifest) exercised
# recover_interrupted_publish(), which deleted whatever sat at a target path
# purely because a marker existed, with no digest check (write-safety-integrity-repair-spec.md
# B1 finding #3). That function is gone; classify_recovery()/cleanup_recoverable_partial()/
# forward_complete_from_marker() replace it. Equivalent and additional coverage,
# including the specific "never delete an unverified file" and "never delete a
# post-publish write" regressions those two tests could not have caught, lives
# in the Package 1 recovery-classification tests below.

def _marker_payload(personal,work,source_digest="source-digest",staged_personal=None,staged_work=None):
    return {"source_digest":source_digest,"started_at":"2026-07-15T00:00:00+00:00",
            "personal":{"path":str(personal.resolve()),"sha256":"p-sha","logical_digest":staged_personal},
            "work":{"path":str(work.resolve()),"sha256":"w-sha","logical_digest":staged_work}}


def test_classify_recovery_is_fresh_when_nothing_exists(tmp_path):
    marker=tmp_path/"manifest.json.publish-pending";manifest=tmp_path/"manifest.json"
    personal=tmp_path/"personal.db";work=tmp_path/"work.db"
    classification,detail=bootstrap.classify_recovery(marker,manifest,personal,work)
    assert classification=="fresh" and detail=={}


def test_classify_recovery_never_deletes_a_foreign_file_it_cannot_verify(tmp_path):
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    marker=tmp_path/"manifest.json.publish-pending"
    personal.write_text("some other file that happens to live at this path")
    marker.write_text(json.dumps(_marker_payload(personal,work,staged_personal="staged-digest-that-will-never-match")))
    classification,detail=bootstrap.classify_recovery(marker,manifest,personal,work)
    assert classification=="foreign_corrupted" and detail["unexpected"]==["personal"]
    before=personal.read_bytes()
    bootstrap.cleanup_recoverable_partial(json.loads(marker.read_text()),personal,work)
    assert personal.exists() and personal.read_bytes()==before


def test_cleanup_recoverable_partial_deletes_only_the_digest_matching_target(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";journal_fixture(source)
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion,apply=False))
    bootstrap.main()
    snapshot=tmp_path/"snap.db";bootstrap.snapshot_source(source,snapshot)
    staged_report=bootstrap.build(snapshot,tmp_path/"p.staging",{"abap-object-exporter","ai-pos","ai-pos-app","personal","smart-timesheet"},instance_id="personal",source_digest="d")
    marker={"source_digest":"d","started_at":"2026-07-15T00:00:00+00:00",
            "personal":{"path":str(personal.resolve()),"sha256":staged_report["sha256"],"logical_digest":staged_report["logical_digest"]},
            "work":{"path":str(work.resolve()),"sha256":"absent","logical_digest":"absent-digest"}}
    os.replace(tmp_path/"p.staging",personal)
    staging_leftover=Path(str(work)+".staging");staging_leftover.write_text("leftover")
    bootstrap.cleanup_recoverable_partial(marker,personal,work)
    assert not personal.exists() and not work.exists() and not staging_leftover.exists()


# --- Full main()-level recovery scenarios (write-safety-integrity-repair-spec.md §10 rows 1-9b, 28) ---

def _run_apply(monkeypatch,source,personal,work,manifest,exclusion):
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main()
    return json.loads(manifest.read_text())


def test_successful_apply_stamps_matching_instance_markers(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    report=_run_apply(monkeypatch,source,personal,work,manifest,exclusion)
    assert report["published"]
    personal_marker=bootstrap.instance_identity.read_journal_marker(sqlite3.connect(personal))
    work_marker=bootstrap.instance_identity.read_journal_marker(sqlite3.connect(work))
    assert personal_marker["instance_id"]=="personal" and work_marker["instance_id"]=="work"
    assert personal_marker["source_digest"]==work_marker["source_digest"]==report["source_logical_digest"]


def test_rerun_after_success_is_idempotent_noop(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    _run_apply(monkeypatch,source,personal,work,manifest,exclusion)
    before_p,before_w,before_m=personal.read_bytes(),work.read_bytes(),manifest.read_bytes()
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main()  # must return normally (exit 0), not raise SystemExit
    assert personal.read_bytes()==before_p and work.read_bytes()==before_w and manifest.read_bytes()==before_m


def test_crash_after_publish_before_manifest_forward_completes_without_touching_targets(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    original=bootstrap.write_json_atomic
    def fail_on_manifest(path,data):
        if Path(path)==manifest:raise RuntimeError("simulated crash right before manifest write")
        return original(path,data)
    monkeypatch.setattr(bootstrap,"write_json_atomic",fail_on_manifest)
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="simulated crash"):bootstrap.main()
    monkeypatch.undo()  # restore write_json_atomic before the retry
    assert personal.exists() and work.exists() and not manifest.exists()
    marker=manifest.with_name(manifest.name+".publish-pending");assert marker.exists()
    before_p,before_w=personal.read_bytes(),work.read_bytes()
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main()
    assert personal.read_bytes()==before_p and work.read_bytes()==before_w
    assert not marker.exists() and json.loads(manifest.read_text())["published"]


def test_post_publish_write_survives_recovery(tmp_path,monkeypatch):
    """§10 row 8 — the exact scenario B1 finding #2 warned about: a write
    landing between a successful publish and the crashed manifest write must
    never be destroyed by the retry that completes the manifest."""
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    original=bootstrap.write_json_atomic
    def fail_on_manifest(path,data):
        if Path(path)==manifest:raise RuntimeError("simulated crash right before manifest write")
        return original(path,data)
    monkeypatch.setattr(bootstrap,"write_json_atomic",fail_on_manifest)
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="simulated crash"):bootstrap.main()
    monkeypatch.undo()
    assert personal.exists() and work.exists() and not manifest.exists()

    con=sqlite3.connect(personal);con.execute("PRAGMA foreign_keys=OFF")
    con.execute("INSERT INTO memory_records VALUES ('rec-post-publish',2,'problem','personal','normal','{}','{}','deadbeef','idem-post-publish','probe-agent','explicit_user_command',NULL,NULL,NULL,1.0,'2026-07-15T00:00:00+00:00','2026-07-15T00:00:00+00:00')")
    con.execute("INSERT INTO memory_events VALUES ('evt-post-publish','rec-post-publish','record_created','2026-07-15T00:00:00+00:00','probe-agent','{}')")
    con.execute("INSERT INTO record_state VALUES ('rec-post-publish','accepted','auto_accepted',NULL,NULL,NULL,NULL,'none',NULL,NULL,'evt-post-publish')")
    con.commit();con.close()

    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main()
    assert sqlite3.connect(personal).execute("SELECT record_id FROM memory_records WHERE record_id='rec-post-publish'").fetchone()
    assert json.loads(manifest.read_text())["published"]


def test_live_instance_is_never_treated_as_a_reset_target(tmp_path,monkeypatch):
    """§4.3 row 3 — once an instance has diverged from what bootstrap
    published (ordinary subsequent use), a rerun must refuse, not overwrite."""
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    _run_apply(monkeypatch,source,personal,work,manifest,exclusion)
    con=sqlite3.connect(personal);con.execute("PRAGMA foreign_keys=OFF")
    con.execute("INSERT INTO memory_records VALUES ('rec-live',2,'problem','personal','normal','{}','{}','deadbeef','idem-live','probe-agent','explicit_user_command',NULL,NULL,NULL,1.0,'2026-07-15T00:00:00+00:00','2026-07-15T00:00:00+00:00')")
    con.execute("INSERT INTO memory_events VALUES ('evt-live','rec-live','record_created','2026-07-15T00:00:00+00:00','probe-agent','{}')")
    con.execute("INSERT INTO record_state VALUES ('rec-live','accepted','auto_accepted',NULL,NULL,NULL,NULL,'none',NULL,NULL,'evt-live')")
    con.commit();con.close()
    before_p,before_w=personal.read_bytes(),work.read_bytes()
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code==3
    assert personal.read_bytes()==before_p and work.read_bytes()==before_w


def test_incompatible_existing_state_refuses_without_deleting(tmp_path,monkeypatch):
    """§4.3 row 4 — target files exist with no marker and no manifest at all."""
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    personal.write_text("some unrelated foreign file");work.write_text("also unrelated")
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code==3
    assert personal.read_text()=="some unrelated foreign file" and work.read_text()=="also unrelated"


def test_exactly_one_target_present_is_incompatible_existing_state(tmp_path,monkeypatch):
    """§4.3 row 4's "exactly one target exists" sub-case."""
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    personal.write_text("only one target exists")
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code==3
    assert personal.read_text()=="only one target exists" and not work.exists()


def test_partition_must_match_control_constants(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";journal_fixture(source)
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    bad_argv=["bootstrap","--source",str(source),"--personal-journal",str(personal),"--work-journal",str(work),
              "--personal-workspaces","ai-pos","--work-workspaces","sap-work,personal","--manifest",str(manifest),"--apply"]
    monkeypatch.setattr(sys,"argv",bad_argv)
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code==2
    report=json.loads(manifest.read_text())
    assert report["operator_audit"]["partition_constant_mismatch"]
    assert not personal.exists() and not work.exists()


# --- Instance-marker enforcement at the writer/reader/projector boundary (§10 rows 9, 9b) ---

def test_journal_writer_refuses_on_instance_mismatch(tmp_path):
    from brain.writer import JournalWriter
    from brain.config import BrainConfig
    journal=tmp_path/"work.db";journal_fixture(journal,instance_id="work")
    before=journal.read_bytes()
    writer=JournalWriter(BrainConfig(journal_db_path=journal,instance_id="personal"))
    with pytest.raises(BrainError,match="BRAIN_INSTANCE_MISMATCH"):writer.connect()
    assert journal.read_bytes()==before  # §10 row 9: refusal before any query, journal byte-identical


def test_journal_writer_refuses_on_missing_marker(tmp_path):
    from brain.writer import JournalWriter
    from brain.config import BrainConfig
    journal=tmp_path/"unmarked.db";journal_fixture(journal)  # no instance_id -> no marker
    before=journal.read_bytes()
    writer=JournalWriter(BrainConfig(journal_db_path=journal,instance_id="personal"))
    with pytest.raises(BrainError,match="BRAIN_INSTANCE_MARKER_MISSING"):writer.connect()
    assert journal.read_bytes()==before  # §10 row 9b: refusal before any query, journal byte-identical


def test_journal_writer_accepts_matching_instance(tmp_path):
    from brain.writer import JournalWriter
    from brain.config import BrainConfig
    journal=tmp_path/"personal.db";journal_fixture(journal,instance_id="personal")
    writer=JournalWriter(BrainConfig(journal_db_path=journal,instance_id="personal"))
    con=writer.connect();con.close()


def test_repository_journal_and_retrieval_refuse_on_instance_mismatch(tmp_path):
    from brain.repository import Repository
    from brain.config import BrainConfig
    journal=tmp_path/"work.db";journal_fixture(journal,instance_id="work")
    before=journal.read_bytes()
    repo=Repository(BrainConfig(journal_db_path=journal,retrieval_db_path=tmp_path/"missing-retrieval.db",instance_id="personal"))
    with pytest.raises(BrainError,match="BRAIN_INSTANCE_MISMATCH"):
        with repo.journal():pass
    assert journal.read_bytes()==before


def test_repository_retrieval_refuses_on_instance_mismatch(tmp_path):
    from brain.repository import Repository
    from brain.config import BrainConfig
    from brain.projector import ProjectorConfig,ProjectionProjector
    journal=tmp_path/"personal.db";journal_fixture(journal,instance_id="personal")
    retrieval=tmp_path/"retrieval.db"
    class FakeEmbedder:
        def embed_document(self,text):return [1.0,0.0,0.0,0.0],"fake"
    projector=ProjectionProjector(ProjectorConfig(journal,retrieval,"fake",4,"fake",instance_id="personal"),FakeEmbedder())
    while projector.run_once(100).status.value=="HEALTHY":pass
    before=retrieval.read_bytes()
    repo=Repository(BrainConfig(journal_db_path=journal,retrieval_db_path=retrieval,instance_id="work"))
    with pytest.raises(BrainError,match="BRAIN_INSTANCE_MISMATCH"):
        with repo.retrieval():pass
    assert retrieval.read_bytes()==before


def test_projector_journal_reader_refuses_on_instance_mismatch(tmp_path):
    from brain.projector.journal_reader import JournalReader
    journal=tmp_path/"work.db";journal_fixture(journal,instance_id="work")
    before=journal.read_bytes()
    reader=JournalReader(journal,instance_id="personal")
    with pytest.raises(BrainError,match="BRAIN_INSTANCE_MISMATCH"):
        with reader.connect():pass
    assert journal.read_bytes()==before


def test_projector_stamps_retrieval_marker_on_first_write_and_enforces_it_thereafter(tmp_path):
    from brain.projector import ProjectorConfig,ProjectionProjector
    journal=tmp_path/"personal.db";journal_fixture(journal,instance_id="personal")
    retrieval=tmp_path/"retrieval.db"
    class FakeEmbedder:
        def embed_document(self,text):return [1.0,0.0,0.0,0.0],"fake"
    projector=ProjectionProjector(ProjectorConfig(journal,retrieval,"fake",4,"fake",instance_id="personal"),FakeEmbedder())
    projector.run_once(1)
    marker=bootstrap.instance_identity.read_retrieval_marker(sqlite3.connect(retrieval))
    assert marker=="personal"
    mismatched=ProjectionProjector(ProjectorConfig(journal,retrieval,"fake",4,"fake",instance_id="work"),FakeEmbedder())
    with pytest.raises(BrainError,match="BRAIN_INSTANCE_MISMATCH"):mismatched.run_once(1)


def test_control_store_write_grant_gate_is_the_row_28_scenario(tmp_path,monkeypatch):
    """§10 row 28, cross-referenced here for traceability; the test itself
    lives in tests/test_brain_control.py::test_write_grant_requires_an_existing_marked_journal
    alongside the rest of ControlStore's own coverage."""
    assert True


# --- Repair pass regressions (docs/reviews/package-1-bootstrap-instance-binding-review.md) ---

def _seed_post_publish_write(journal):
    con=sqlite3.connect(journal);con.execute("PRAGMA foreign_keys=OFF")
    con.execute("INSERT INTO memory_records VALUES ('rec-post-publish',2,'problem','personal','normal','{}','{}','deadbeef','idem-post-publish','probe-agent','explicit_user_command',NULL,NULL,NULL,1.0,'2026-07-15T00:00:00+00:00','2026-07-15T00:00:00+00:00')")
    con.execute("INSERT INTO memory_events VALUES ('evt-post-publish','rec-post-publish','record_created','2026-07-15T00:00:00+00:00','probe-agent','{}')")
    con.execute("INSERT INTO record_state VALUES ('rec-post-publish','accepted','auto_accepted',NULL,NULL,NULL,NULL,'none',NULL,NULL,'evt-post-publish')")
    con.commit();con.close()


def test_B1_half_published_target_with_post_publish_write_is_never_clobbered_by_retry(tmp_path,monkeypatch):
    """Blocking B-1 (Fable review Probe 1): a hard crash lands BETWEEN the two
    os.replace calls in publish_pair — only "personal" is published, "work" is
    not — a legitimate write then commits into the published personal
    journal, and the operator retries. The retry must refuse (never exit 0)
    and must never overwrite the committed write; the state is
    recoverable_partial with a surviving (digest-diverged) target, which
    cleanup correctly does not delete, and main() must not fall through to a
    fresh build/publish over it."""
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    original_replace=bootstrap.os.replace;calls=0
    def crash_between_replaces(src,dst):
        nonlocal calls
        if Path(dst) in (personal,work):
            calls+=1
            if calls==2:raise KeyboardInterrupt("hard crash between the two publishes")
        return original_replace(src,dst)
    monkeypatch.setattr(bootstrap.os,"replace",crash_between_replaces)
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(BaseException):bootstrap.main()
    monkeypatch.undo()
    assert personal.exists() and not work.exists() and not manifest.exists()
    marker=manifest.with_name(manifest.name+".publish-pending");assert marker.exists()
    assert bootstrap.classify_recovery(marker,manifest,personal,work)[0]=="recoverable_partial"

    _seed_post_publish_write(personal)

    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code!=0  # must never silently succeed (was exit 0 before the fix)
    assert error.value.code==4
    survived=sqlite3.connect(personal).execute("SELECT record_id FROM memory_records WHERE record_id='rec-post-publish'").fetchone()
    assert survived is not None, "B-1 regression: the retry must never destroy a committed post-publish write"
    assert not work.exists() and not manifest.exists()
    assert marker.exists(), "the publish-pending marker must be preserved in the recoverable_partial_cleanup_incomplete refusal state, not deleted"

    # The marker being preserved means the next retry re-derives the same
    # precise classification (recoverable_partial), not a degraded generic
    # incompatible_existing_state — and stays exactly as fail-closed.
    personal_bytes_before=personal.read_bytes()
    assert bootstrap.classify_recovery(marker,manifest,personal,work)[0]=="recoverable_partial"
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code==4
    assert marker.exists()
    assert personal.read_bytes()==personal_bytes_before and not work.exists() and not manifest.exists()


def test_B1_target_appearing_between_build_and_publish_is_refused_toctou(tmp_path,monkeypatch):
    """Blocking B-1 TOCTOU guard: a target materializes (concurrent process,
    restored backup, operator mistake) in the window between staging/build
    completing and publish_pair actually running. Must refuse, must not
    overwrite the surviving file, must not publish either target."""
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    original_build=bootstrap.build;calls=0
    def sneaky_build(*args,**kwargs):
        nonlocal calls;calls+=1
        result=original_build(*args,**kwargs)
        if calls==2:  # "work" build just finished; a target appears out of nowhere
            personal.write_text("a target that appeared out of nowhere")
        return result
    monkeypatch.setattr(bootstrap,"build",sneaky_build)
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="appeared before publish"):bootstrap.main()
    assert personal.read_text()=="a target that appeared out of nowhere"
    assert not work.exists() and not manifest.exists()
    assert not manifest.with_name(manifest.name+".publish-pending").exists()


def test_H1_dry_run_never_overwrites_a_published_manifest(tmp_path,monkeypatch):
    """High H-1 (Fable review Probe 2): a plain dry run over an already
    completed bootstrap must leave the published manifest byte-identical and
    route its own report to a sibling preflight file; the next --apply rerun
    must still be the idempotent already_bootstrapped no-op."""
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    _run_apply(monkeypatch,source,personal,work,manifest,exclusion)
    before=manifest.read_bytes()
    assert json.loads(before).get("published") is True

    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion,apply=False))
    bootstrap.main()
    assert manifest.read_bytes()==before, "H-1 regression: dry run must never overwrite a published manifest"
    preflight=manifest.with_name(manifest.name+".preflight.json")
    assert preflight.exists()
    assert json.loads(preflight.read_text()).get("manifest_preserved") is True

    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main()  # must return normally (idempotent already_bootstrapped), not raise
    assert manifest.read_bytes()==before


def test_M1_forward_completion_records_recovered_observation(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    original=bootstrap.write_json_atomic
    def fail_on_manifest(path,data):
        if Path(path)==manifest:raise RuntimeError("simulated crash right before manifest write")
        return original(path,data)
    monkeypatch.setattr(bootstrap,"write_json_atomic",fail_on_manifest)
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="simulated crash"):bootstrap.main()
    monkeypatch.undo()
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    bootstrap.main()
    report=json.loads(manifest.read_text())
    observation=report["recovered_observation"]
    for role in ("personal","work"):
        assert observation[role]["exists"] is True
        assert observation[role]["integrity_check"]=="ok"
        assert observation[role]["foreign_key_violations"]==[]
        assert observation[role]["marker"]["instance_id"]==role
        assert observation[role]["logical_digest"]==report["result_journal_digests"][role]


def test_M1_forward_completion_refuses_when_foreign_key_check_fails(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    original=bootstrap.write_json_atomic
    def fail_on_manifest(path,data):
        if Path(path)==manifest:raise RuntimeError("simulated crash right before manifest write")
        return original(path,data)
    monkeypatch.setattr(bootstrap,"write_json_atomic",fail_on_manifest)
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="simulated crash"):bootstrap.main()
    monkeypatch.undo()
    assert personal.exists() and work.exists() and not manifest.exists()

    # Corrupt the published personal journal so it fails PRAGMA foreign_key_check.
    con=sqlite3.connect(personal);con.execute("PRAGMA foreign_keys=OFF")
    con.execute("INSERT INTO memory_events VALUES ('evt-dangling','rec-does-not-exist','record_created','2026-07-15T00:00:00+00:00','agent','{}')")
    con.commit();con.close()
    assert sqlite3.connect(personal).execute("PRAGMA foreign_key_check").fetchall()

    marker=manifest.with_name(manifest.name+".publish-pending")
    classification,detail=bootstrap.classify_recovery(marker,manifest,personal,work)
    assert classification=="corrupted"
    assert any(issue["target"]=="personal" and issue["issue"]=="foreign_key_violations" for issue in detail["issues"])

    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code==4
    assert not manifest.exists()  # forward-completion must not have run


def test_M1_forward_completion_refuses_when_workspace_partition_is_violated(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";journal_fixture(source);exclusion=approved_exclusion(source,tmp_path/"exclusion.json")
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    original=bootstrap.write_json_atomic
    def fail_on_manifest(path,data):
        if Path(path)==manifest:raise RuntimeError("simulated crash right before manifest write")
        return original(path,data)
    monkeypatch.setattr(bootstrap,"write_json_atomic",fail_on_manifest)
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(RuntimeError,match="simulated crash"):bootstrap.main()
    monkeypatch.undo()
    assert personal.exists() and work.exists() and not manifest.exists()

    # A record with a foreign (sap-work) workspace lands in the published
    # personal journal — a partition violation forward-completion must catch.
    con=sqlite3.connect(personal);con.execute("PRAGMA foreign_keys=OFF")
    con.execute("INSERT INTO memory_records VALUES ('rec-foreign-ws',2,'problem','sap-work','sensitive','{}','{}','h','k','agent','explicit_user_command',NULL,NULL,NULL,1.0,'2026-07-15T00:00:00+00:00','2026-07-15T00:00:00+00:00')")
    con.execute("INSERT INTO memory_events VALUES ('evt-foreign-ws','rec-foreign-ws','record_created','2026-07-15T00:00:00+00:00','agent','{}')")
    con.execute("INSERT INTO record_state VALUES ('rec-foreign-ws','accepted','auto_accepted',NULL,NULL,NULL,NULL,'none',NULL,NULL,'evt-foreign-ws')")
    con.commit();con.close()

    marker=manifest.with_name(manifest.name+".publish-pending")
    classification,detail=bootstrap.classify_recovery(marker,manifest,personal,work)
    assert classification=="corrupted"
    assert any(issue["target"]=="personal" and issue["issue"]=="workspace_partition_violation" and "sap-work" in issue["detail"] for issue in detail["issues"])

    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest,exclusion))
    with pytest.raises(SystemExit) as error:bootstrap.main()
    assert error.value.code==4
    assert not manifest.exists()


def test_H2_build_brain_m1_indexes_script_passes_instance_id_to_projector():
    """High H-2: the documented index-build script must pass --instance-id to
    run_brain_projector.py, or the resulting index is silently built under
    the legacy exemption and later refused by every marked reader."""
    text=(ROOT/"scripts/build_brain_m1_indexes.sh").read_text()
    assert '--instance-id "$instance"' in text
    # the flag must be part of the same invocation, not merely present somewhere else in the file
    lines=text.splitlines()
    call_index=next(i for i,line in enumerate(lines) if "run_brain_projector.py" in line)
    call_block="\n".join(lines[call_index:call_index+3])
    assert '--instance-id "$instance"' in call_block


def test_H2_run_brain_projector_instance_id_flag_enforces_marker_via_plan(tmp_path):
    """The --plan path is read-only (never calls the embedding endpoint), so
    this exercises the real CLI -> ProjectorConfig(instance_id=...) wiring
    end-to-end without needing a live embedding server."""
    matching=tmp_path/"personal.db";journal_fixture(matching,instance_id="personal")
    ok=subprocess.run([sys.executable,str(ROOT/"scripts/run_brain_projector.py"),"--journal-db",str(matching),
                       "--retrieval-db",str(tmp_path/"r1.db"),"--instance-id","personal","--plan"],
                      capture_output=True,text=True)
    assert ok.returncode==0,ok.stderr
    assert json.loads(ok.stdout)["plan"]["status"] in ("HEALTHY","NO_CHANGES")

    mismatched=tmp_path/"work.db";journal_fixture(mismatched,instance_id="work")
    refused=subprocess.run([sys.executable,str(ROOT/"scripts/run_brain_projector.py"),"--journal-db",str(mismatched),
                            "--retrieval-db",str(tmp_path/"r2.db"),"--instance-id","personal","--plan"],
                           capture_output=True,text=True)
    assert refused.returncode!=0
    assert "BRAIN_INSTANCE_MISMATCH" in refused.stderr


def test_M3_smoke_brain_m1_write_end_to_end_against_disposable_paths(tmp_path):
    """Medium M-3: the documented write-smoke step must complete end to end
    against fresh disposable paths, and must never leak its temporary
    BRAIN_*_JOURNAL_DB env override into the parent process."""
    journal=tmp_path/"smoke-journal.db";control=tmp_path/"smoke-control.db"
    env={k:v for k,v in os.environ.items() if not k.startswith("BRAIN_")}
    before_env=dict(os.environ)
    result=subprocess.run([sys.executable,str(ROOT/"scripts/smoke_brain_m1_write.py"),
                           "--journal-db",str(journal),"--control-db",str(control),
                           "--instance","personal","--workspace","personal"],
                          capture_output=True,text=True,env=env,cwd=ROOT)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report["result"]["status"]=="accepted" and report["disposable"] is True
    assert dict(os.environ)==before_env  # subprocess env changes never touch the parent
    assert instance_identity.read_journal_marker(sqlite3.connect(journal))["instance_id"]=="personal"
