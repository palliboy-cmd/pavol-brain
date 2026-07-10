"""Build-scoped Falkor drivers without graphiti-core's constructor task."""
import re,sys
from graphiti_core.driver import falkordb_driver as fd
def safe_name(value): return re.sub(r'[^A-Za-z0-9_]+','_',value).strip('_')
class SequentialFalkorDriver(fd.FalkorDriver):
 def __init__(self,host='localhost',port=6379,username=None,password=None,falkor_db=None,database='default_db',base_database=None,logical_group=None):
  fd.GraphDriver.__init__(self);self.base_database=base_database or database;self.logical_group=logical_group;self.host=host;self.port=port;self.username=username;self.password=password;self._database=self.effective_database(self.base_database,logical_group);self._initialized=False;self._initializing=False
  print(f'SEQUENTIAL_FALKOR_DRIVER_CREATED class={type(self).__name__} id={id(self)} database={self._database} base={self.base_database} group={logical_group}',file=sys.stderr)
  self.client=falkor_db if falkor_db is not None else fd.FalkorDB(host=host,port=port,username=username,password=password)
  self._entity_node_ops=fd.FalkorEntityNodeOperations();self._episode_node_ops=fd.FalkorEpisodeNodeOperations();self._community_node_ops=fd.FalkorCommunityNodeOperations();self._saga_node_ops=fd.FalkorSagaNodeOperations();self._entity_edge_ops=fd.FalkorEntityEdgeOperations();self._episodic_edge_ops=fd.FalkorEpisodicEdgeOperations();self._community_edge_ops=fd.FalkorCommunityEdgeOperations();self._has_episode_edge_ops=fd.FalkorHasEpisodeEdgeOperations();self._next_episode_edge_ops=fd.FalkorNextEpisodeEdgeOperations();self._search_ops=fd.FalkorSearchOperations();self._graph_ops=fd.FalkorGraphMaintenanceOperations()
 @staticmethod
 def effective_database(base,group): return safe_name(base) if not group else f'{safe_name(base)}__{safe_name(group)}'
 def clone(self,database):
  raise RuntimeError(f'unexpected_graphiti_driver_clone: requested={database} source={self._database}')
 async def ensure_initialized(self):
  if self._initialized:return
  if self._initializing:return
  self._initializing=True
  try:
   await super().build_indices_and_constraints();await super().execute_query('MATCH (n) RETURN 1 LIMIT 1');self._initialized=True
  finally:self._initializing=False
 async def execute_query(self,cypher_query_,**kwargs):
  if not self._initialized and not self._initializing:await self.ensure_initialized()
  return await super().execute_query(cypher_query_,**kwargs)
