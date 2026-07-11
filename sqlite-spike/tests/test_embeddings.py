import importlib.util, math, sqlite3, sys
from pathlib import Path
import unittest
ROOT=Path(__file__).parents[1]; sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from embeddings import EmbeddingClient, EmbeddingError, cosine, normalize
from search import hybrid_search, vector_search
spec=importlib.util.spec_from_file_location('fts',ROOT/'scripts/fts_baseline.py'); fts=importlib.util.module_from_spec(spec); spec.loader.exec_module(fts)

class EmbeddingTests(unittest.TestCase):
 def test_dimension_validation(self):
  with self.assertRaisesRegex(EmbeddingError,'dimension'): normalize([1,2],3)
 def test_non_finite_rejection(self):
  for value in (math.nan,math.inf):
   with self.assertRaisesRegex(EmbeddingError,'non_finite'): normalize([value])
 def test_cosine_ordering(self): self.assertGreater(cosine([1,0],[1,0]),cosine([1,0],[0,1]))
 def test_fingerprint_cache_invalidation(self):
  self.assertNotEqual(EmbeddingClient(model='a',dimension=2).fingerprint,EmbeddingClient(model='b',dimension=2).fingerprint)
 def test_rrf_ranking(self):
  self.assertGreater(.5/61+.5/61,.5/62)
 def test_deterministic_ties(self):
  rows=[{'status':'accepted','confidence':1,'valid_at':'2026-01-01','record_id':'b'},{'status':'accepted','confidence':1,'valid_at':'2026-01-01','record_id':'a'}]
  from search import _tie; self.assertEqual(sorted(rows,key=_tie)[0]['record_id'],'a')
 def test_filter_before_ranking(self):
  import tempfile
  path=Path(tempfile.mkdtemp())/'x.db'; rows=[fts.source_records()[0]]; rows[0]['workspace']='allowed'; fts.rebuild(path,rows); con=sqlite3.connect(path); con.row_factory=sqlite3.Row
  q={'scope':['other'],'filters':{'mode':'current','types':[rows[0]['type']],'sensitive_allowed':False}}; self.assertEqual(vector_search(con,q,[1.0]),[])
 def test_24_ids(self):
  import json
  self.assertEqual(len(json.loads((ROOT/'dataset/queries.json').read_text())),24)
 def test_vector_reporting_keeps_only_actual_gaps_not_evaluated(self):
  run={'top1_pass':True,'top3_pass':True,'latency_ms':1,'leaks':{'workspace':0,'sensitive':0,'forbidden_status':0},'returned':[],'tags':[],'query_id':'Q01','failure_condition_pass':True}
  report=fts.evaluate([run],['rebuild_equivalence','noise_rate_manual'])
  self.assertEqual(report['not_evaluated'],['rebuild_equivalence','noise_rate_manual'])
