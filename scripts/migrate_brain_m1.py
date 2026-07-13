#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from brain.migrations import inspect_m1,migrate_m1

def main():
    p=argparse.ArgumentParser(description="Inspect or backup-and-migrate a canonical journal to M1 schema v2")
    p.add_argument("--journal-db",type=Path,required=True);p.add_argument("--backup",type=Path)
    p.add_argument("--apply",action="store_true")
    a=p.parse_args()
    if a.apply and not a.backup:p.error("--apply requires an explicit --backup path")
    report=migrate_m1(a.journal_db,a.backup) if a.apply else inspect_m1(a.journal_db)
    print(json.dumps(report,ensure_ascii=False,indent=2,default=str))
if __name__=="__main__":main()
