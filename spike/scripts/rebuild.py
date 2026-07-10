#!/usr/bin/env python3
"""Clean, build-scoped replay; it never deletes another FalkorDB graph."""
import argparse,json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from src.config import RESULTS
def run(args): return subprocess.run([sys.executable,*args],check=True,capture_output=True,text=True).stdout
def latest(pattern):
 files=sorted(RESULTS.glob(pattern));return json.loads(files[-1].read_text())
def main():
 p=argparse.ArgumentParser();p.add_argument('--build-a',default='build-a');p.add_argument('--build-b',default='build-b');p.add_argument('--db',default='spike.db');a=p.parse_args()
 # A is existing baseline; B is reset only inside its own spike_build_b graph.
 run(['scripts/query.py','--build-id',a.build_a,'--db',a.db])
 run(['scripts/project.py','--build-id',a.build_b,'--db',a.db,'--reset'])
 run(['scripts/query.py','--build-id',a.build_b,'--db',a.db])
 first=latest('*-'+a.build_b+'-projection/projection.json')
 run(['scripts/project.py','--build-id',a.build_b,'--db',a.db])
 second=latest('*-'+a.build_b+'-projection/projection.json')
 base=latest('*-'+a.build_a+'-projection/projection.json');qa=latest('*-'+a.build_a+'-queries/query-results.json');qb=latest('*-'+a.build_b+'-queries/query-results.json')
 deterministic_equal=base['counts']['eligible']==first['counts']['eligible'] and base['counts']['projected']==first['counts']['projected']
 retrieval_equal=[x['expected_top_pass'] for x in qa['queries']]==[x['expected_top_pass'] for x in qb['queries']]
 report={'build_a':a.build_a,'build_b':a.build_b,'build_b_database':'spike_'+a.build_b,'deterministic_counts_equal':deterministic_equal,'retrieval_expected_top_equivalent':retrieval_equal,'second_replay_skipped_idempotent':second['counts']['skipped_idempotent'],'idempotent':second['counts']['failed']==0 and second['counts']['skipped_idempotent']==second['counts']['eligible']}
 out=RESULTS/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-rebuild');out.mkdir(parents=True);(out/'rebuild.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
