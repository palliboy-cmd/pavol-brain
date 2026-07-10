#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from src.config import DB,DATASET
from src.journal import init,insert,append_event
def main():
 p=argparse.ArgumentParser(); p.add_argument('--db',default=DB); p.add_argument('--dataset',default=DATASET); a=p.parse_args(); con=init(a.db); inserted=skipped=0
 for line in Path(a.dataset).read_text().splitlines():
  r=json.loads(line); rid=insert(con,r)
  if not rid: skipped+=1; continue
  inserted+=1
  if r['expected']['status']=='rejected': append_event(con,rid,'record_rejected',{'reason':'dataset case'})
  if r['expected']['status']=='superseded': append_event(con,rid,'record_superseded',{'superseded_by':'rec-046','invalid_at':'2026-07-10T00:00:00+00:00','reason':'synthetic correction'})
 con.commit(); print(json.dumps({'inserted':inserted,'idempotent_skipped':skipped,'db':str(a.db)}))
if __name__=='__main__': main()
