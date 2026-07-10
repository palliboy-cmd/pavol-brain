#!/usr/bin/env python3
"""Run dataset benchmark queries against one build-scoped Graphiti graph."""
import argparse,asyncio,json,sys,time
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from src.config import DB,RESULTS
from src.journal import connect
from src.graphiti_adapter import Adapter
from src.projection import build_database
def record_ids_for_edges(con, edges, build_id):
    ids=[]
    for edge in edges:
        row=con.execute('SELECT record_id FROM graph_edges WHERE edge_uuid=? AND build_id=?',(edge.uuid,build_id)).fetchone()
        if row: ids.append(row['record_id']); continue
        for episode_uuid in edge.episodes:
            row=con.execute('SELECT record_id FROM projection_map WHERE episode_uuid=? AND build_id=?',(episode_uuid,build_id)).fetchone()
            if row: ids.append(row['record_id']); break
    return ids
async def main():
 p=argparse.ArgumentParser();p.add_argument('--build-id',required=True);p.add_argument('--db',default=DB);p.add_argument('--queries',default='dataset/queries.json');a=p.parse_args(); con=connect(a.db);adapter=Adapter(build_database(a.build_id)); results=[]
 try:
  await adapter.initialize(workspaces=sorted({group for query in json.loads(Path(a.queries).read_text()) for group in query['scope']}))
  for query in json.loads(Path(a.queries).read_text()):
   groups=query['scope'];started=time.perf_counter(); edges=await adapter.search(query['query'],groups); latency=(time.perf_counter()-started)*1000; ids=record_ids_for_edges(con,edges,a.build_id); returned=[]
   for rank,(edge,rid) in enumerate(zip(edges,ids),1):
    row=con.execute('SELECT r.workspace,s.status FROM memory_records r JOIN record_state s ON s.record_id=r.record_id WHERE r.record_id=?',(rid,)).fetchone()
    if not row: continue
    returned.append({'record_id':rid,'rank':rank,'edge_uuid':edge.uuid,'provenance':'deterministic' if con.execute('SELECT 1 FROM graph_edges WHERE edge_uuid=?',(edge.uuid,)).fetchone() else 'extracted','state':row['status'],'detected_workspace':row['workspace']})
   wanted=set(query['expected_top']); actual=[r['record_id'] for r in returned]
   results.append({'query_id':query['id'],'query':query['query'],'scope':groups,'filters':query.get('filters',{}),'returned':returned,'latency_ms':latency,'expected_top':query['expected_top'],'expected_top_pass':bool(wanted & set(actual)),'top1_pass':bool(actual and actual[0] in wanted),'failure_condition':query['failure_condition'],'failure_condition_pass':all(r['state'] not in ('candidate','rejected','forgotten') and r['detected_workspace'] in groups for r in returned)})
  report={'build_id':a.build_id,'database':build_database(a.build_id),'queries':results};out=RESULTS/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+a.build_id+'-queries');out.mkdir(parents=True);(out/'query-results.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));return 0
 finally: await adapter.close()
if __name__=='__main__':raise SystemExit(asyncio.run(main()))
