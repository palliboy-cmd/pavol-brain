#!/usr/bin/env python3
"""Day-1 N1/N2 proof against a live FalkorDB; emits machine-readable evidence."""
import asyncio,json,sys,traceback
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from src.graphiti_adapter import Adapter, profile_summary
async def main():
 out={'graphiti_core':'0.29.2','N1':{'status':'not-evaluated'},'N2':{'status':'not-evaluated'}}; a=None
 try:
  out['profile']=profile_summary()
  a=Adapter('checkpoint')
  await a.initialize(reset=True,workspaces=('checkpoint-a','workspace-a','workspace-b'))
  # N1: direct CRUD mutation and retrieval after persistence.
  eid,_=await a.explicit_triplet('checkpoint-a','N1 source','N1 target','RELATES_TO','N1 explicit fact')
  before=await a.edge(eid); saved=await a.invalidate(eid); after=await a.edge(eid)
  hits=await a.search('N1 explicit fact','checkpoint-a')
  out['N1']={'status':'pass' if after.invalid_at and after.expired_at else 'fail','edge_uuid':eid,'before':{'invalid_at':str(before.invalid_at),'expired_at':str(before.expired_at)},'after':{'invalid_at':str(after.invalid_at),'expired_at':str(after.expired_at)},'search_contains_invalidated':eid in [x.uuid for x in hits]}
  # N2: same names/triplet in distinct groups and a retry in A.
  e_a,_=await a.explicit_triplet('workspace-a','Shared name','Shared artifact','TOUCHES','same triplet', '11111111-1111-1111-1111-111111111111')
  e_b,_=await a.explicit_triplet('workspace-b','Shared name','Shared artifact','TOUCHES','same triplet', '22222222-2222-2222-2222-222222222222')
  retry_error=None
  try: await a.explicit_triplet('workspace-a','Shared name','Shared artifact','TOUCHES','same triplet',e_a)
  except Exception as exc: retry_error=type(exc).__name__
  a_hits=await a.search('same triplet','workspace-a'); b_hits=await a.search('same triplet','workspace-b')
  au={x.uuid for x in a_hits}; bu={x.uuid for x in b_hits}
  out['N2']={'status':'pass' if e_a in au and e_b in bu and e_b not in au and e_a not in bu else 'fail','a_edge':e_a,'b_edge':e_b,'a_results':list(au),'b_results':list(bu),'retry_error':retry_error}
 except Exception as exc:
  out['error']=f'{type(exc).__name__}: {exc}'; out['traceback']=traceback.format_exc()
  if type(exc).__name__ == 'ConnectionError': out['infrastructure_status']='runtime_unavailable'
 finally:
  if a: await a.close()
 print(json.dumps(out,indent=2)); destination=Path('results')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-checkpoint'); destination.mkdir(parents=True,exist_ok=True); (destination/'checkpoint.json').write_text(json.dumps(out,indent=2))
 return 0 if out['N1']['status']=='pass' and out['N2']['status']=='pass' else 2
if __name__=='__main__': raise SystemExit(asyncio.run(main()))
