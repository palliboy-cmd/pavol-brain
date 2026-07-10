import tempfile,unittest
from pathlib import Path
from src.journal import init,insert,append_event
class JournalTests(unittest.TestCase):
 def test_fold_materialization_and_idempotency(self):
  with tempfile.TemporaryDirectory() as d:
   c=init(Path(d)/'x.db');r={'record_id':'r1','type':'fact','workspace':'personal','sensitivity':'normal','payload':{'subject':'a','predicate':'is','object':'b'},'source_assertion':'imported_curated'}
   self.assertEqual(insert(c,r),'r1');self.assertIsNone(insert(c,r));append_event(c,'r1','record_forgotten',{'reason':'test'});c.commit();self.assertEqual(c.execute('select status from record_state').fetchone()[0],'forgotten')
