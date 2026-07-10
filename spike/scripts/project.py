#!/usr/bin/env python3
import argparse,asyncio,json,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from src.config import DB,RESULTS
from src.journal import connect
from src.graphiti_adapter import Adapter
from src.projection import build_database,eligible_records,project_record
async def main():
 p=argparse.ArgumentParser();p.add_argument('--build-id',required=True);p.add_argument('--db',default=DB);p.add_argument('--reset',action='store_true');p.add_argument('--limit',type=int);p.add_argument('--record-id');p.add_argument('--confirm-full',action='store_true');a=p.parse_args()
 if not (a.limit or a.record_id or a.confirm_full): p.error('full projection requires --confirm-full; use --limit 1 for the compatibility gate')
 con=connect(a.db); adapter=Adapter(build_database(a.build_id))
 try:
  rows=eligible_records(con)
  if a.record_id: rows=[row for row in rows if row['record_id']==a.record_id]
  if a.limit: rows=rows[:a.limit]
  await adapter.initialize(reset=a.reset,workspaces=sorted({row['workspace'] for row in rows}),log=lambda state: print(state,file=sys.stderr))
  results=[]
  for row in rows:
   print(f'projecting {row["record_id"]}',file=sys.stderr); outcome=await project_record(con,adapter,row,a.build_id); results.append(outcome);print(f'{outcome["status"]} {row["record_id"]}',file=sys.stderr)
  report={'build_id':a.build_id,'database':build_database(a.build_id),'profile':adapter.profile,'records':results,'counts':{'eligible':len(rows),'projected':sum(x['status']=='projected' for x in results),'skipped_idempotent':sum(x['status']=='skipped_idempotent' for x in results),'failed':sum(x['status']=='failed' for x in results)}}
  out=RESULTS/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+a.build_id+'-projection');out.mkdir(parents=True);(out/'projection.json').write_text(json.dumps(report,indent=2,default=str));print(json.dumps(report,indent=2,default=str)); return 0 if not report['counts']['failed'] else 2
 finally: await adapter.close()
if __name__=='__main__':raise SystemExit(asyncio.run(main()))
