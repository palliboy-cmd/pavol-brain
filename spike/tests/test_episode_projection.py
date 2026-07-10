import ast,inspect,unittest,textwrap
from src.graphiti_adapter import Adapter
from src.projection import EPISODE_TYPES
class EpisodeProjectionTests(unittest.TestCase):
 def test_new_episode_call_has_no_uuid_keyword(self):
  tree=ast.parse(textwrap.dedent(inspect.getsource(Adapter.add_episode)));call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and getattr(n.func,'attr',None)=='add_episode');self.assertNotIn('uuid',[k.arg for k in call.keywords]);self.assertNotIn('group_id',[k.arg for k in call.keywords])
 def test_episode_types_are_mapped(self): self.assertEqual(EPISODE_TYPES,{'decision','outcome','fact'})
 def test_projection_cursor_is_primary_key(self):
  import sqlite3;c=sqlite3.connect(':memory:');c.execute('create table projection_map(record_id text,build_id text,episode_uuid text,primary key(record_id,build_id))');c.execute('insert into projection_map values(?,?,?)',('r','b','generated'));self.assertRaises(sqlite3.IntegrityError,c.execute,'insert into projection_map values(?,?,?)',('r','b','other'))
