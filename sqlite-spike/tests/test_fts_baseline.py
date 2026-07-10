import importlib.util, json, sqlite3, tempfile
from pathlib import Path
import unittest

MODULE=Path(__file__).parents[1]/'scripts'/'fts_baseline.py'
spec=importlib.util.spec_from_file_location('fts_baseline',MODULE); fts=importlib.util.module_from_spec(spec); spec.loader.exec_module(fts)

def rec(rid='r1',kind='fact',workspace='a',status='accepted',sensitivity='normal',projection=True):
    payload={'subject':'alpha','predicate':'states','object':'mini core','evidence':'proof'}
    if kind=='decision': payload={'statement':'Alpha decision','rationale':'proof','decision_status':'accepted','artifacts':[]}
    if kind=='outcome': payload={'summary':'Alpha outcome','changes':['change'],'verification':{'tests':'ok'},'artifacts':[]}
    if kind=='artifact_link': payload={'source_record':'r1','artifact_uri':'repo://a/README.md','relation':'touches','origin_claim':'deterministic'}
    return {'record_id':rid,'type':kind,'workspace':workspace,'sensitivity':sensitivity,'payload':payload,'source_assertion':'imported_curated','expected':{'status':status,'projection':projection,'artifact_validation':'valid'},'status':status,'valid_at':'2026-01-01T00:00:00+00:00','invalid_at':None,'confidence':1.0}
def query(scope=['a'],mode='current',sensitive=False,types=['fact']): return {'query':'alpha mini','scope':scope,'filters':{'mode':mode,'types':types,'sensitive_allowed':sensitive}}

class FtsBaselineTests(unittest.TestCase):
 def db(self,rows):
  path=Path(tempfile.mkdtemp())/'r.db'; fts.rebuild(path,rows); return sqlite3.connect(path)
 def test_canonical_text_determinism(self): self.assertEqual(fts.canonical_text(rec()),fts.canonical_text(rec()))
 def test_eligibility(self): self.assertTrue(fts.eligible(rec(),'current')); self.assertFalse(fts.eligible(rec(status='candidate',projection=False),'historical'))
 def test_forbidden_statuses(self): self.assertFalse(fts.eligible(rec(status='rejected',projection=False),'historical'))
 def test_workspace_isolation(self):
  con=self.db([rec('a',workspace='a'),rec('b',workspace='b')]); self.assertEqual([x['record_id'] for x in fts.search(con,query())],['a'])
 def test_sensitive_filtering(self):
  con=self.db([rec('s',sensitivity='sensitive')]); self.assertEqual(fts.search(con,query()),[]); self.assertEqual([x['record_id'] for x in fts.search(con,query(sensitive=True))],['s'])
 def test_fts_tokenization(self):
  con=self.db([rec()]); self.assertEqual([x['record_id'] for x in fts.search(con,query())],['r1'])
 def test_duplicate_prevention(self):
  con=self.db([rec(),rec()]); self.assertEqual(con.execute('select count(*) from retrieval_documents').fetchone()[0],1)
 def test_historical_record_retrieval(self):
  old=rec(status='superseded'); con=self.db([old]); self.assertEqual(fts.search(con,query(mode='current')),[]); self.assertEqual([x['record_id'] for x in fts.search(con,query(mode='historical'))],['r1'])
 def test_exact_preservation_of_24_query_ids(self):
  qs=json.loads((fts.ROOT/'sqlite-spike/dataset/queries.json').read_text()); self.assertEqual([q['id'] for q in qs],[f'Q{i:02d}' for i in range(1,25)])
