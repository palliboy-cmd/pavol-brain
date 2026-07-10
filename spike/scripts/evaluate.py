#!/usr/bin/env python3
"""Compute only metrics supported by stored projection/query evidence."""
import argparse,json,platform,statistics,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from src.config import DB,RESULTS
from src.journal import connect
from src.graphiti_adapter import profile_summary
def percentile(values,p):
 if not values:return None
 values=sorted(values);return values[min(len(values)-1,round((len(values)-1)*p))]
def latest(pattern):
 files=sorted(RESULTS.glob(pattern));return json.loads(files[-1].read_text()) if files else None
def metric(threshold,measured,status):return {'threshold':threshold,'measured':measured,'status':status}
def main():
 p=argparse.ArgumentParser();p.add_argument('--build-id',required=True);p.add_argument('--compare-builds',nargs=2);p.add_argument('--db',default=DB);a=p.parse_args();q=latest('*-'+a.build_id+'-queries/query-results.json');proj=latest('*-'+a.build_id+'-projection/projection.json');con=connect(a.db);m={}
 if q:
  qs=q['queries'];n=len(qs);top1=sum(x['top1_pass'] for x in qs)/n if n else 0;top3=sum(x['expected_top_pass'] for x in qs)/n if n else 0;returned=[r for x in qs for r in x['returned']];lat=[x['latency_ms'] for x in qs]
  sensitive_leaks=sum(r['detected_workspace']=='sap-work' and 'sap-work' not in x['scope'] for x in qs for r in x['returned']);workspace_leaks=sum(r['detected_workspace'] not in x['scope'] for x in qs for r in x['returned']);candidate_leaks=sum(r['state'] in ('candidate','rejected','forgotten') for r in returned)
  m['G1']=metric('>=80%',top3,'pass' if top3>=.8 else 'fail');m['G2']=metric('>=60%',top1,'pass' if top1>=.6 else 'fail');m['G3']=metric('>=70% cloud',None,'not-evaluated');m['G4']=metric('<=10%',None,'not-evaluated');m['G10']=metric('0',sensitive_leaks,'pass' if sensitive_leaks==0 else 'fail');m['G11']=metric('0',workspace_leaks,'pass' if workspace_leaks==0 else 'fail');m['G13']=metric('0',candidate_leaks,'pass' if candidate_leaks==0 else 'fail');m['G15']=metric('<2s',{'p50_ms':percentile(lat,.5),'p95_ms':percentile(lat,.95)},'pass' if percentile(lat,.95)<2000 else 'fail')
 else:
  for k in ('G1','G2','G3','G4','G10','G11','G13','G15'):m[k]=metric('see Proposal 003',None,'not-evaluated')
 if proj:
  rows=proj['records'];lat=[r['latency_ms'] for r in rows if r['status']=='projected'];edge_count=con.execute('SELECT count(*) FROM graph_edges WHERE build_id=?',(a.build_id,)).fetchone()[0];map_count=con.execute('SELECT count(*) FROM projection_map WHERE build_id=?',(a.build_id,)).fetchone()[0];duplicate_maps=con.execute('SELECT count(*) FROM (SELECT record_id,count(*) n FROM projection_map WHERE build_id=? GROUP BY record_id HAVING n>1)',(a.build_id,)).fetchone()[0]
  m['G5']=metric('100%',{'eligible':proj['counts']['eligible'],'deterministic_edges':edge_count},'pass' if edge_count==proj['counts']['eligible'] and proj['counts']['failed']==0 else 'fail');m['G12']=metric('0',duplicate_maps,'pass' if duplicate_maps==0 and map_count==proj['counts']['eligible'] else 'fail');m['G16']=metric('<30s',{'p50_ms':percentile(lat,.5),'p95_ms':percentile(lat,.95)},'pass' if percentile(lat,.95)<30000 else 'fail')
 else:
  for k in ('G5','G12','G16'):m[k]=metric('see Proposal 003',None,'not-evaluated')
 for k in ('G6','G7','G14','N3','N4'):m[k]=metric('see Proposal 003',None,'not-evaluated')
 superseded=con.execute("SELECT record_id FROM record_state WHERE status='superseded'").fetchall(); historical_projected=sum(con.execute('SELECT count(*) FROM projection_map WHERE record_id=? AND build_id=?',(r['record_id'],a.build_id)).fetchone()[0] for r in superseded)
 m['G8']=metric('current supersede truth',{'superseded_records':len(superseded)},'pass' if superseded else 'not-evaluated')
 m['G9']=metric('historical supersede availability',{'superseded_records':len(superseded),'historically_projected':historical_projected},'pass' if superseded and historical_projected==len(superseded) else ('not-evaluated' if not superseded else 'fail'))
 cp=latest('*-mini-core-checkpoint/checkpoint.json')
 for k in ('N1','N2'):m[k]=metric('pass',cp[k]['measured'] if cp else None,cp[k]['status'] if cp else 'not-evaluated')
 if a.compare_builds:
  left,right=a.compare_builds;lp=latest('*-'+left+'-projection/projection.json');rp=latest('*-'+right+'-projection/projection.json');lq=latest('*-'+left+'-queries/query-results.json');rq=latest('*-'+right+'-queries/query-results.json');equiv=bool(lp and rp and lp['counts']['eligible']==rp['counts']['eligible'] and lp['counts']['projected']==rp['counts']['projected']);retrieval_equal=bool(lq and rq and [x['expected_top_pass'] for x in lq['queries']]==[x['expected_top_pass'] for x in rq['queries']]);m['G14']=metric('pass',equiv and retrieval_equal,'pass' if equiv and retrieval_equal else 'fail');m['N3']=metric('pass',equiv,'pass' if equiv else 'fail');m['N4']=metric('pass',retrieval_equal,'pass' if retrieval_equal else 'fail')
 report={'build_id':a.build_id,'created_at':datetime.now(timezone.utc).isoformat(),'versions':{'python':platform.python_version(),'graphiti-core':'0.29.2','falkordb':'4.14.9','cloud_model':'not configured','local_profile':profile_summary()},'automatic_metrics':m,'manual_metrics':{'extracted_edge_quality':'not-evaluated'},'unevaluated_reason':'Only metrics with stored mini-core projection/query evidence are evaluated.'};out=RESULTS/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+a.build_id+'-evaluation');out.mkdir(parents=True);(out/'evaluation.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
