"""The only Graphiti seam. Explicit edges are never labelled extracted."""
import os, uuid
import asyncio,sys
from datetime import datetime, timezone
from graphiti_core import Graphiti
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.nodes import EntityNode
from graphiti_core.edges import EntityEdge
from .local_llm import LocalOpenAIGenericClient
from .sequential_falkor import SequentialFalkorDriver

def utcnow(): return datetime.now(timezone.utc)

class ModelConfigurationMissing(RuntimeError):
    """Raised before connecting when the selected Graphiti profile is incomplete."""

def _required_env(name, default):
    value = os.getenv(name, default).strip()
    if not value:
        raise ModelConfigurationMissing(f'model_configuration_missing: {name} is required')
    return value

def local_profile():
    """Return non-secret local OpenAI-compatible settings, with safe local defaults."""
    profile = _required_env('GRAPHITI_PROFILE', 'local')
    if profile not in ('local','cloud'):
        raise ModelConfigurationMissing(f'model_configuration_missing: unsupported GRAPHITI_PROFILE={profile!r}')
    embedding_dim = _required_env('GRAPHITI_EMBEDDING_DIM', '768')
    try:
        embedding_dim = int(embedding_dim)
    except ValueError as exc:
        raise ModelConfigurationMissing('model_configuration_missing: GRAPHITI_EMBEDDING_DIM must be an integer') from exc
    return {
        'profile': profile,
        'base_url': _required_env('GRAPHITI_BASE_URL', 'http://localhost:11434/v1' if profile=='local' else ''),
        'api_key': _required_env('GRAPHITI_API_KEY', 'ollama' if profile=='local' else ''),
        'llm_model': _required_env('GRAPHITI_LLM_MODEL', 'qwen3.6:35b-mlx' if profile=='local' else ''),
        'small_model': _required_env('GRAPHITI_SMALL_MODEL', 'qwen3.6:35b-mlx' if profile=='local' else ''),
        'embedder_model': _required_env('GRAPHITI_EMBEDDER_MODEL', 'nomic-embed-text:latest' if profile=='local' else ''),
        'embedding_dim': embedding_dim,
    }

def profile_summary():
    config = local_profile()
    return {key: config[key] for key in ('profile', 'base_url', 'llm_model', 'small_model', 'embedder_model', 'embedding_dim')}

class Adapter:
    def __init__(self, database='spike'):
        self.database=database;self.workspace_clients={};self.edge_drivers={};self._create_client()
    def _create_client(self):
        self.profile = profile_summary()
        self.driver=None;self.graph=None
    async def close(self):
        for client in self.workspace_clients.values(): await client['graph'].close()
        pending=self.pending_index_tasks();print(f'pending_index_tasks={len(pending)}',file=sys.stderr)
        if pending: raise RuntimeError('pending_falkor_index_tasks_detected')
    def pending_index_tasks(self):
        current=asyncio.current_task();found=[]
        for task in asyncio.all_tasks():
            if task is current or task.done(): continue
            name=getattr(task.get_coro(),'__qualname__',repr(task.get_coro()))
            if 'build_indices_and_constraints' in name or 'FalkorDriver' in name: found.append(name)
        return found
    def workspace_client(self,group_id):
        if group_id not in self.workspace_clients:
            config=local_profile();llm=LLMConfig(api_key=config['api_key'],base_url=config['base_url'],model=config['llm_model'],small_model=config['small_model']);embed=OpenAIEmbedderConfig(api_key=config['api_key'],base_url=config['base_url'],embedding_model=config['embedder_model'],embedding_dim=config['embedding_dim'])
            driver=SequentialFalkorDriver(host=os.getenv('FALKORDB_HOST','localhost'),port=int(os.getenv('FALKORDB_PORT','6379')),base_database=self.database,logical_group=group_id)
            llm_client=LocalOpenAIGenericClient(config=llm,structured_output_mode='json_object') if config['profile']=='local' else OpenAIGenericClient(config=llm)
            graph=Graphiti(graph_driver=driver,llm_client=llm_client,embedder=OpenAIEmbedder(config=embed),cross_encoder=OpenAIRerankerClient(config=llm))
            self.workspace_clients[group_id]={'driver':driver,'graph':graph}
            print(f'WORKSPACE_GRAPHITI_CLIENT workspace={group_id} graph_id={id(graph)} driver_id={id(driver)} database={driver._database}',file=sys.stderr)
        return self.workspace_clients[group_id]
    def workspace_driver(self,group_id): return self.workspace_client(group_id)['driver']
    async def initialize(self, *, reset=False, workspaces=(), log=lambda _: None):
        if reset:
            log('reset_started');await self.close();self.workspace_clients={};self.edge_drivers={};self._create_client()
            for group in workspaces: await self.workspace_driver(group).execute_query('MATCH (n) DETACH DELETE n')
            log('reset_completed')
        log('client_created')
        for group in workspaces:
            driver=self.workspace_driver(group);log(f'indices_build_started {driver._database}');await driver.ensure_initialized();log(f'indices_build_completed {driver._database}');await driver.health_check();await self.smoke_test(driver);log(f'smoke_test_completed {driver._database}')
    async def smoke_test(self,driver):
        name='smoke:'+uuid.uuid4().hex;node=EntityNode(name=name,group_id='_spike_smoke',labels=['Record'])
        await node.save(driver);loaded=await EntityNode.get_by_uuid(driver,node.uuid)
        if loaded.uuid != node.uuid: raise RuntimeError('smoke_test_failed: node read mismatch')
        await node.delete(driver)
    async def explicit_triplet(self, group_id, source_name, target_name, name, fact, edge_uuid=None):
        driver=self.workspace_driver(group_id);await driver.ensure_initialized()
        source=EntityNode(uuid=str(uuid.uuid5(uuid.NAMESPACE_URL,group_id+':'+source_name)),name=source_name,group_id=group_id,labels=['Record'])
        target=EntityNode(uuid=str(uuid.uuid5(uuid.NAMESPACE_URL,group_id+':'+target_name)),name=target_name,group_id=group_id,labels=['Artifact'])
        print(f'ASSERTS source uuid={source.uuid} group={group_id} database={driver._database}',file=sys.stderr);await source.save(driver);print(f'ASSERTS source_saved uuid={source.uuid}',file=sys.stderr)
        print(f'ASSERTS source_readback uuid={(await EntityNode.get_by_uuid(driver,source.uuid)).uuid}',file=sys.stderr)
        print(f'ASSERTS target uuid={target.uuid} group={group_id} database={driver._database}',file=sys.stderr);await target.save(driver);print(f'ASSERTS target_saved uuid={target.uuid}',file=sys.stderr)
        print(f'ASSERTS target_readback uuid={(await EntityNode.get_by_uuid(driver,target.uuid)).uuid}',file=sys.stderr)
        if (await EntityNode.get_by_uuid(driver,source.uuid)).uuid != source.uuid: raise RuntimeError('source_node_not_persisted')
        if (await EntityNode.get_by_uuid(driver,target.uuid)).uuid != target.uuid: raise RuntimeError('target_node_not_persisted')
        edge=EntityEdge(uuid=edge_uuid or str(uuid.uuid4()),group_id=group_id,source_node_uuid=source.uuid,target_node_uuid=target.uuid,created_at=utcnow(),name=name,fact=fact,valid_at=utcnow(),reference_time=utcnow(),attributes={'origin':'explicit'})
        await edge.generate_embedding(self.workspace_client(group_id)['graph'].embedder);result=await edge.save(driver);loaded=await EntityEdge.get_by_uuid(driver,edge.uuid);self.edge_drivers[edge.uuid]=driver
        if loaded.uuid != edge.uuid: raise RuntimeError('edge_not_persisted')
        print(f'ASSERTS edge_readback uuid={loaded.uuid}',file=sys.stderr);return edge.uuid,result
    async def edge(self, edge_uuid): return await EntityEdge.get_by_uuid(self.edge_drivers[edge_uuid],edge_uuid)
    async def invalidate(self, edge_uuid):
        edge=await self.edge(edge_uuid); edge.invalid_at=utcnow(); edge.expired_at=utcnow(); await edge.save(self.edge_drivers[edge_uuid]); return edge
    async def add_episode(self, *, record_id, build_id, group_id, body, reference_time):
        client=self.workspace_client(group_id);expected=client['driver'];await expected.ensure_initialized();graph=client['graph'];before_id=id(graph.driver);before_database=graph.driver._database
        print(f'EPISODE_DRIVER_BEFORE class={type(graph.driver).__name__} id={before_id} database={before_database} workspace={group_id}',file=sys.stderr)
        result=await graph.add_episode(name=f'record:{record_id}', episode_body=body,
            source_description='pavol-brain spike journal record', reference_time=reference_time,
            update_communities=False)
        print(f'EPISODE_DRIVER_AFTER class={type(graph.driver).__name__} id={id(graph.driver)} database={graph.driver._database}',file=sys.stderr)
        if id(graph.driver)!=before_id or graph.driver._database!=before_database: raise RuntimeError('graphiti_driver_mutated')
        return result
    async def cleanup_episode_fragments(self, group_id, name):
        driver=self.workspace_driver(group_id);records,_,_=await driver.execute_query('MATCH (e:Episodic {name: $name}) RETURN e.uuid AS uuid',name=name)
        from graphiti_core.nodes import EpisodicNode
        for record in records: await (await EpisodicNode.get_by_uuid(driver,record['uuid'])).delete(driver)
        return len(records)
    async def search(self, query, group_ids):
        results=[]
        for group_id in group_ids:
            client=self.workspace_client(group_id);await client['driver'].ensure_initialized();results.extend(await client['graph'].search(query,group_ids=['_'],num_results=10))
        return results
