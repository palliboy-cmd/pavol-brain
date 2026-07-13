import asyncio
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT))

from brain.api import Brain
from brain.config import BrainConfig
from brain.mcp_server import CapabilityPolicy,create_server
from brain.control import ControlStore,IntegrationProfile,RegistryPolicy,READ_TOOLS,TOOLS
from brain.projector import ProjectorConfig,ProjectionProjector
from brain.projector.models import ProjectionStatus
from brain import artifact_validation as av

class QueryTransport:
    def embed(self,text):return [1.0,1.0,1.0,1.0]

class Embedder:
    def embed_document(self,text):
        seed=int(hashlib.sha256(text.encode()).hexdigest()[:8],16)
        return [float((seed>>(i*4))%11+1) for i in range(4)],"acceptance-fake"

def init_journal(path):
    con=sqlite3.connect(path);con.executescript((ROOT/"spike/schema/journal.sql").read_text());av.apply_migration(con);con.close()

def brain(journal,retrieval,identity,instance):
    return Brain(BrainConfig(journal_db_path=journal,retrieval_db_path=retrieval,embedding_dimension=4,
                             endpoint_probe_timeout=.01,client_identity=identity,instance_id=instance),QueryTransport())

def project(journal,retrieval):
    projector=ProjectionProjector(ProjectorConfig(journal,retrieval,"acceptance-fake",4,"acceptance-fake"),Embedder())
    while projector.run_once(100).status==ProjectionStatus.HEALTHY:pass

def call(server,name,args):
    content=asyncio.run(server.call_tool(name,args));return json.loads(content[0].text)

def test_closed_memory_loop_two_profiles_and_two_instances(tmp_path):
    personal_journal=tmp_path/"personal-journal.db";personal_retrieval=tmp_path/"personal-retrieval.db"
    work_journal=tmp_path/"work-journal.db";work_retrieval=tmp_path/"work-retrieval.db"
    init_journal(personal_journal);init_journal(work_journal)
    store=ControlStore(tmp_path/"control.db")
    def registry_profile(identity,instance,workspaces,sensitive=(),write=False):
        p=IntegrationProfile(identity,identity,"custom_mcp","local_stdio","local",True,list(workspaces),list(sensitive),
            list(TOOLS if write else READ_TOOLS),identity,write_enabled=write,brain_instance=instance)
        store.save(p,reason="acceptance fixture")
        return RegistryPolicy(store,identity,instance_id=instance,runtime_identity=identity)

    agent_a_brain=brain(personal_journal,personal_retrieval,"agent-a","personal")
    seed=agent_a_brain.record_decision(statement="M1 must close the memory loop",rationale="Agent handoff is the proof",
        alternatives=[],verdict="accepted",reason="final direction",reopen_when=None,evidence=["doc://brain-direction/m1"],
        source_assertion="explicit_user_confirmation",workspace="personal",idempotency_key="seed-context")
    project(personal_journal,personal_retrieval)
    agent_a=create_server(brain=agent_a_brain,policy=registry_profile("agent-a","personal",["personal"],write=True))

    loaded=call(agent_a,"brain_search",{"query":"closed memory loop","types":["decision"]})
    assert seed.record_id in [row["record_id"] for row in loaded["results"]]
    outcome_args={"summary":"M1 write path implemented","changes":["journal writer","MCP tools"],
        "verification":{"tests":"pass"},"artifacts":["repo://pavol-brain/brain/api.py"],
        "source_assertion":"verified_tool_result","idempotency_key":"acceptance-outcome",
        "links":[{"target_record_id":seed.record_id,"relation":"implements"}]}
    outcome=call(agent_a,"brain_record_outcome",outcome_args)
    assert outcome["status"]=="accepted" and outcome["workspace"]=="personal"
    repeated=call(agent_a,"brain_record_outcome",outcome_args)
    assert repeated["record_id"]==outcome["record_id"] and repeated["idempotent"]
    decision=call(agent_a,"brain_record_decision",{"statement":"Personal and WORK use separate instances",
        "rationale":"Zero leak by construction","alternatives":[{"option":"one shared journal","verdict":"rejected",
        "reason":"larger policy surface","reopen_when":"cross-instance retrieval is explicitly required","evidence":["doc://m1/isolation"]}],
        "verdict":"accepted","reason":"safe M1 boundary","evidence":["doc://m1/isolation"],
        "source_assertion":"explicit_user_confirmation","idempotency_key":"acceptance-decision"})
    assert decision["status"]=="accepted"
    project(personal_journal,personal_retrieval)

    agent_b_brain=brain(personal_journal,personal_retrieval,"agent-b","personal")
    agent_b=create_server(brain=agent_b_brain,policy=registry_profile("agent-b","personal",["personal"]))
    found=call(agent_b,"brain_search",{"query":"M1 write path handoff","types":["outcome","decision"],"limit":10})
    ids={row["record_id"] for row in found["results"]}
    assert outcome["record_id"] in ids and decision["record_id"] in ids
    loaded_outcome=call(agent_b,"brain_get_record",{"record_id":outcome["record_id"]})
    assert loaded_outcome["payload"]["summary"]=="M1 write path implemented"
    denied=call(agent_b,"brain_record_outcome",{"summary":"must fail"})
    assert denied["error"]["code"]=="BRAIN_WRITE_DISABLED"

    work_brain=brain(work_journal,work_retrieval,"work-agent","work")
    work_server=create_server(brain=work_brain,policy=registry_profile("work-agent","work",["sap-work"],["sap-work"],True))
    work_outcome=call(work_server,"brain_record_outcome",{"summary":"WORK-only outcome","artifacts":["repo://pavol-brain/brain/api.py"],
        "source_assertion":"verified_tool_result","idempotency_key":"work-outcome"})
    assert work_outcome["workspace"]=="sap-work" and work_outcome["status"]=="accepted"
    assert sqlite3.connect(work_journal).execute("SELECT sensitivity FROM memory_records WHERE record_id=?",(work_outcome["record_id"],)).fetchone()[0]=="sensitive"
    no_sensitive=create_server(brain=work_brain,policy=CapabilityPolicy(frozenset({"sap-work"}),profile="unsafe-work",write_enabled=True))
    assert call(no_sensitive,"brain_record_outcome",{"summary":"must fail"})["error"]["code"]=="BRAIN_SENSITIVE_SCOPE_DENIED"
    project(work_journal,work_retrieval)
    assert call(agent_a,"brain_search",{"query":"WORK-only","workspaces":["sap-work"]})["error"]["code"]=="BRAIN_WORKSPACE_DENIED"
    assert call(work_server,"brain_search",{"query":"personal M1","workspaces":["personal"]})["error"]["code"]=="BRAIN_WORKSPACE_DENIED"
    assert sqlite3.connect(personal_journal).execute("SELECT count(*) FROM memory_records WHERE workspace='sap-work'").fetchone()[0]==0
    assert sqlite3.connect(work_journal).execute("SELECT count(*) FROM memory_records WHERE workspace='personal'").fetchone()[0]==0
