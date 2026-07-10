import sqlite3,unittest
from src.projection import build_database,EPISODE_TYPES
from src.policy import projectable
class ProjectionTests(unittest.TestCase):
 def test_build_namespace(self): self.assertEqual(build_database('build-a'),'spike_build_a')
 def test_episode_record_types(self): self.assertEqual(EPISODE_TYPES,{'decision','outcome','fact'})
 def test_exclusion(self):
  for status in ('candidate','rejected','forgotten'): self.assertFalse(projectable({'status':status,'projection':'none'}))
  self.assertTrue(projectable({'status':'accepted','projection':'none'}))
 def test_idempotency_key_is_per_build(self):
  c=sqlite3.connect(':memory:');c.execute('create table projection_map(record_id text,build_id text,episode_uuid text,primary key(record_id,build_id))');c.execute('insert into projection_map values(?,?,?)',('r','a','e'));self.assertRaises(sqlite3.IntegrityError,c.execute,'insert into projection_map values(?,?,?)',('r','a','e2'))
  c.execute('insert into projection_map values(?,?,?)',('r','b','e2'))
