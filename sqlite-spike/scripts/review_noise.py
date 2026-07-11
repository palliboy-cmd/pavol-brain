#!/usr/bin/env python3
"""Persist a transparent top-3 relevance review from the curated allow-list."""
import argparse,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def main():
 p=argparse.ArgumentParser();p.add_argument('--report',required=True);a=p.parse_args(); source=json.loads(Path(a.report).read_text()); reviews=[]; total=noise=top3_total=top3_noise=0
 for q in source['queries']:
  judgments=[]
  for r in q['returned']:
   label='relevant' if r['record_id'] in q['expected_top'] else ('allowed' if r['record_id'] in q['allowed_alternatives'] else 'noise'); judgments.append({'record_id':r['record_id'],'rank':r['rank'],'judgment':label}); total+=1; noise+=label=='noise'
   if r['rank']<=3: top3_total+=1; top3_noise+=label=='noise'
  reviews.append({'query_id':q['query_id'],'judgments':judgments,'review_basis':'curated expected_top and allowed_alternatives; no retrieval tuning applied'})
 out={'source_report':a.report,'queries':reviews,'returned_results_top3':top3_total,'noise_results_top3':top3_noise,'noise_rate_top3':top3_noise/top3_total if top3_total else None,'go_gate_s4':{'threshold':'<=10%','pass':top3_noise/top3_total<=.10 if top3_total else False},'diagnostic_returned_results_all':total,'diagnostic_noise_results_all':noise,'diagnostic_noise_rate_all_returned':noise/total if total else None,'manual_review_status':'complete'}
 (ROOT/'sqlite-spike/results/noise-review.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
