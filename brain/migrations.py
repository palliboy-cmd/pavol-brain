"""Explicit, backup-first canonical journal migrations."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
M1_SQL = ROOT / "spike" / "schema" / "m1_record_types.sql"
OLD_TYPES = {"decision","outcome","fact","correction","artifact_link","preference"}
PRESERVED_TABLES = ("memory_records","memory_events","record_state","artifact_links",
                    "artifact_validation_events","artifact_validation_state")

def _digest_rows(con, table):
    digest=hashlib.sha256()
    for row in con.execute(f"SELECT * FROM {table} ORDER BY 1"):
        digest.update(json.dumps(tuple(row),ensure_ascii=False,separators=(",",":")).encode())
    return digest.hexdigest()

def inspect_m1(path):
    path=Path(path)
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)) as con:
        con.row_factory=sqlite3.Row
        tables={row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required={"memory_records","memory_events","record_state","artifact_links"}
        missing=sorted(required-tables)
        integrity=con.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys=[tuple(row) for row in con.execute("PRAGMA foreign_key_check")]
        types={row[0]:row[1] for row in con.execute("SELECT type,count(*) FROM memory_records GROUP BY type")} if not missing else {}
        sql=con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_records'").fetchone()
        table_digests={table:_digest_rows(con,table) for table in PRESERVED_TABLES if table in tables}
        combined=hashlib.sha256(json.dumps(table_digests,sort_keys=True).encode()).hexdigest()
        return {"path":str(path),"missing_tables":missing,"integrity_check":integrity,"foreign_key_violations":foreign_keys,
                "types":types,"record_count":sum(types.values()),"already_m1":bool(sql and "'problem'" in sql[0] and "'analysis'" in sql[0]),
                "record_digest":table_digests.get("memory_records"),"table_digests":table_digests,"canonical_digest":combined}

def migrate_m1(path, backup_path):
    path=Path(path);before=inspect_m1(path)
    if before["missing_tables"] or before["integrity_check"]!="ok" or before["foreign_key_violations"]:
        raise RuntimeError("journal preflight failed")
    unknown=set(before["types"])-OLD_TYPES-{"problem","analysis"}
    if unknown:raise RuntimeError(f"unknown record types: {sorted(unknown)}")
    if before["already_m1"]:return {"changed":False,"backup":None,"before":before,"after":before,"message":"already migrated"}
    backup_path=Path(backup_path)
    if backup_path.exists():raise FileExistsError(backup_path)
    backup_path.parent.mkdir(parents=True,exist_ok=True)
    source=sqlite3.connect(path);backup=sqlite3.connect(backup_path)
    source.backup(backup);backup.close();source.close()
    if inspect_m1(backup_path)["canonical_digest"]!=before["canonical_digest"]:raise RuntimeError("backup verification failed")
    con=sqlite3.connect(path)
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.executescript(M1_SQL.read_text())
    except Exception:
        con.rollback();con.close()
        restore=sqlite3.connect(backup_path);target=sqlite3.connect(path);restore.backup(target);target.close();restore.close()
        raise
    finally:
        try:con.close()
        except Exception:pass
    after=inspect_m1(path)
    if after["integrity_check"]!="ok" or after["foreign_key_violations"] or after["table_digests"]!=before["table_digests"]:
        restore=sqlite3.connect(backup_path);target=sqlite3.connect(path);restore.backup(target);target.close();restore.close()
        raise RuntimeError("post-migration verification failed; backup restored")
    return {"changed":True,"backup":str(backup_path),"before":before,"after":after}
