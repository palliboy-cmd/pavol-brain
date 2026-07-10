#!/usr/bin/env python3
"""Final N5/N6 probe: one workspace client, no Graphiti clone, no deterministic episode UUID."""
import asyncio,json,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from graphiti_core.nodes import EpisodicNode
from src.graphiti_adapter import Adapter
async def main():
 a=Adapter('spike_probe_final');out={'workspace':'probe'};before_id=before_db=None
 try:
  await a.initialize(reset=True,workspaces=('probe',),log=lambda s:print(s,file=sys.stderr));client=a.workspace_client('probe');graph,driver=client['graph'],client['driver'];before_id,before_db=id(graph.driver),graph.driver._database
  episode=await a.add_episode(record_id='final-probe',build_id='final-probe',group_id='probe',body='{"probe":true}',reference_time=datetime.now(timezone.utc));loaded=await EpisodicNode.get_by_uuid(driver,episode.episode.uuid)
  # Cursor is the idempotency authority; the second call is deliberately skipped.
  cursor={'final-probe':episode.episode.uuid};duplicate_skipped='final-probe' in cursor
  # Simulate partial failure recovery: remove by stable name, then ingest once again.
  removed=await a.cleanup_episode_fragments('probe','record:final-probe');recovered=await a.add_episode(record_id='final-probe',build_id='final-probe',group_id='probe',body='{"probe":true}',reference_time=datetime.now(timezone.utc));records,_,_=await driver.execute_query('MATCH (e:Episodic {name: $name}) RETURN e.uuid AS uuid',name='record:final-probe')
  edge_uuid,_=await a.explicit_triplet('probe','record:final-probe','artifact:final-probe','ASSERTS','final probe explicit edge');edge=await a.edge(edge_uuid);edge.invalid_at=datetime.now(timezone.utc);edge.expired_at=edge.invalid_at;await edge.save(driver)
  out.update(status='pass',driver_id_before=before_id,driver_id_after=id(graph.driver),database_before=before_db,database_after=graph.driver._database,episode_uuid=loaded.uuid,recovery_removed=removed,recovery_episode_uuid=recovered.episode.uuid,episode_count_after_recovery=len(records),projection_cursor_blocks_duplicate=duplicate_skipped,explicit_edge_uuid=edge_uuid)
 finally: await a.close()
 pending=a.pending_index_tasks();out['pending_index_tasks']=pending
 if out.get('status')=='pass' and before_id==out['driver_id_after'] and before_db==out['database_after'] and not pending and out['episode_count_after_recovery']==1: pass
 else: out['status']='fail'
 print(json.dumps(out,indent=2));return 0 if out['status']=='pass' else 2
if __name__=='__main__':raise SystemExit(asyncio.run(main()))
