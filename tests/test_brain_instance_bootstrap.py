import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from journal_fixture import journal_fixture

ROOT=Path(__file__).parents[1]
SPEC=importlib.util.spec_from_file_location("bootstrap_brain_instances",ROOT/"scripts/bootstrap_brain_instances.py")
bootstrap=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(bootstrap)
PARTITION=["--personal-workspaces","abap-object-exporter,ai-pos,ai-pos-app,personal,smart-timesheet","--work-workspaces","sap-work"]


def safe_fixture(path):
    journal_fixture(path)
    con=sqlite3.connect(path)
    row=con.execute("SELECT payload FROM memory_records WHERE record_id='rec-056'").fetchone()
    payload=json.loads(row[0]);payload["source_record"]="rec-053";raw=json.dumps(payload,sort_keys=True,separators=(",",":"))
    con.execute("UPDATE memory_records SET payload=?,raw_input=? WHERE record_id='rec-056'",(raw,raw));con.commit();con.close()


def argv(source,personal,work,manifest,apply=True):
    values=["bootstrap","--source",str(source),"--personal-journal",str(personal),"--work-journal",str(work),*PARTITION,"--manifest",str(manifest)]
    if apply:values.append("--apply")
    return values


def test_cross_payload_reference_rec_056_is_a_blocking_audit_finding(tmp_path):
    source=tmp_path/"legacy.db";journal_fixture(source);manifest=tmp_path/"manifest.json"
    command=[sys.executable,str(ROOT/"scripts/bootstrap_brain_instances.py"),"--source",str(source),
             "--personal-journal",str(tmp_path/"personal.db"),"--work-journal",str(tmp_path/"work.db"),*PARTITION,
             "--manifest",str(manifest),"--apply"]
    result=subprocess.run(command,capture_output=True,text=True)
    assert result.returncode==2
    rows=json.loads(manifest.read_text())["cross_partition_references"]
    finding=next(row for row in rows if row["source_record"]=="rec-056" and row["target_record"]=="rec-001")
    assert finding["field_path"]=="payload.source_record"
    assert finding["source_workspace"]=="sap-work" and finding["target_workspace"]=="ai-pos"
    assert not (tmp_path/"personal.db").exists() and not (tmp_path/"work.db").exists()


def test_safe_split_preserves_source_and_publishes_complete_pair(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json"
    safe_fixture(source);before=source.read_bytes();monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest))
    bootstrap.main();report=json.loads(manifest.read_text())
    assert source.read_bytes()==before and report["source_sha256_before"]==report["source_sha256_after"]
    assert report["personal"]["counts"]["memory_records"]==51 and report["work"]["counts"]["memory_records"]==4
    assert report["published"] and report["result_journal_digests"]
    assert sqlite3.connect(personal).execute("SELECT count(*) FROM memory_records WHERE workspace='sap-work'").fetchone()[0]==0
    assert sqlite3.connect(work).execute("SELECT count(*) FROM memory_records WHERE workspace!='sap-work'").fetchone()[0]==0


def test_second_build_failure_cleans_staging_and_retry_succeeds(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";safe_fixture(source)
    original=bootstrap.build;calls=0
    def fail_second(*args,**kwargs):
        nonlocal calls;calls+=1
        if calls==2:raise RuntimeError("injected second build failure")
        return original(*args,**kwargs)
    monkeypatch.setattr(bootstrap,"build",fail_second);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest))
    with pytest.raises(RuntimeError,match="second build"):bootstrap.main()
    assert not personal.exists() and not work.exists() and not Path(str(personal)+".staging").exists() and not Path(str(work)+".staging").exists()
    monkeypatch.setattr(bootstrap,"build",original);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest))
    bootstrap.main();assert personal.exists() and work.exists()


def test_count_gate_failure_publishes_nothing(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";safe_fixture(source)
    original=bootstrap.build;calls=0
    def wrong_count(*args,**kwargs):
        nonlocal calls;calls+=1;report=original(*args,**kwargs)
        if calls==2:report["counts"]["memory_events"]-=1
        return report
    monkeypatch.setattr(bootstrap,"build",wrong_count);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest))
    with pytest.raises(RuntimeError,match="count mismatch"):bootstrap.main()
    assert not personal.exists() and not work.exists()


def test_integrity_or_fk_gate_failure_publishes_nothing(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";safe_fixture(source)
    monkeypatch.setattr(bootstrap.av,"verify_state",lambda con:[{"injected":"mismatch"}])
    monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest))
    with pytest.raises(RuntimeError,match="validation failed"):bootstrap.main()
    assert not personal.exists() and not work.exists()


def test_reported_fk_failure_publishes_nothing(tmp_path,monkeypatch):
    source=tmp_path/"legacy.db";personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";safe_fixture(source)
    original=bootstrap.build;calls=0
    def reported_fk(*args,**kwargs):
        nonlocal calls;calls+=1;report=original(*args,**kwargs)
        if calls==2:report["foreign_key_violations"]=[("injected",)]
        return report
    monkeypatch.setattr(bootstrap,"build",reported_fk);monkeypatch.setattr(sys,"argv",argv(source,personal,work,manifest))
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


def test_retry_recovers_a_crash_marker_and_partial_target(tmp_path):
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";marker=tmp_path/"manifest.json.publish-pending"
    personal.write_text("partial")
    marker.write_text(json.dumps({"targets":[str(personal.resolve()),str(work.resolve())]}))
    assert bootstrap.recover_interrupted_publish(marker,personal,work)=="cleaned"
    assert not marker.exists() and not personal.exists() and not work.exists()


def test_retry_keeps_a_completed_pair_if_crash_happened_after_manifest(tmp_path):
    personal=tmp_path/"personal.db";work=tmp_path/"work.db";manifest=tmp_path/"manifest.json";marker=tmp_path/"manifest.json.publish-pending"
    personal.write_text("personal");work.write_text("work");manifest.write_text('{"published":true}')
    marker.write_text(json.dumps({"targets":[str(personal.resolve()),str(work.resolve())]}))
    assert bootstrap.recover_interrupted_publish(marker,personal,work,manifest)=="completed"
    assert personal.exists() and work.exists() and not marker.exists()
