#!/usr/bin/env python3
import argparse,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from src.config import DB
from src.journal import connect,fold
def main():
 p=argparse.ArgumentParser();p.add_argument('--db',default=DB);a=p.parse_args();con=connect(a.db); bad=[]
 for row in con.execute('SELECT record_id FROM memory_records'):
  rid=row['record_id']; got=dict(con.execute('SELECT * FROM record_state WHERE record_id=?',(rid,)).fetchone()); expected=fold(con.execute('SELECT * FROM memory_events WHERE record_id=? ORDER BY rowid',(rid,)).fetchall())
  if any(got.get(k)!=v for k,v in expected.items()): bad.append(rid)
 print(f'state fold: {"PASS" if not bad else "FAIL"}; mismatches={bad}'); raise SystemExit(bool(bad))
if __name__=='__main__':main()
