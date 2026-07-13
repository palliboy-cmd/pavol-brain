#!/usr/bin/env python3
"""Registry-backed write smoke for a disposable M1 staging journal."""
import argparse, asyncio, json, sqlite3, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from brain.api import Brain
from brain.config import BrainConfig
from brain.control import ControlStore,IntegrationProfile,RegistryPolicy,TOOLS
from brain.mcp_server import create_server
from brain import artifact_validation as av


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--journal-db",type=Path,required=True);p.add_argument("--control-db",type=Path,required=True)
    p.add_argument("--instance",choices=("personal","work"),required=True);p.add_argument("--workspace",required=True)
    p.add_argument("--identity",default="m1-staging-smoke");p.add_argument("--output",type=Path)
    a=p.parse_args()
    if a.journal_db.exists() or a.control_db.exists():raise SystemExit("smoke targets must be fresh disposable paths")
    a.journal_db.parent.mkdir(parents=True,exist_ok=True)
    con=sqlite3.connect(a.journal_db);con.executescript((ROOT/"spike/schema/journal.sql").read_text());av.apply_migration(con);con.close()
    store=ControlStore(a.control_db);sensitive=[a.workspace] if a.instance=="work" else []
    profile=IntegrationProfile(a.identity,a.identity,"custom_mcp","local_stdio","staging",True,[a.workspace],sensitive,list(TOOLS),a.identity,
                               write_enabled=True,brain_instance=a.instance)
    store.save(profile,actor="m1-smoke",reason="disposable staging write smoke")
    config=BrainConfig(journal_db_path=a.journal_db,retrieval_db_path=a.journal_db.with_name("unused-retrieval.db"),
                       client_identity=a.identity,instance_id=a.instance)
    brain=Brain(config);policy=RegistryPolicy(store,a.identity,instance_id=a.instance,runtime_identity=a.identity)
    server=create_server(config=config,brain=brain,policy=policy)
    async def call():
        result=await server.call_tool("brain_record_outcome",{"summary":"[M1 STAGING SMOKE] registry-backed write",
            "artifacts":["repo://pavol-brain/brain/api.py"],"source_assertion":"verified_tool_result",
            "idempotency_key":f"m1-staging-smoke:{a.instance}:{a.identity}"})
        return json.loads(result[0].text)
    result=asyncio.run(call())
    if "error" in result or result.get("status")!="accepted":raise RuntimeError(result)
    row=sqlite3.connect(a.journal_db).execute("SELECT agent_id,workspace,sensitivity FROM memory_records WHERE record_id=?",(result["record_id"],)).fetchone()
    report={"result":result,"profile":{"identity":a.identity,"instance":a.instance,"workspace":a.workspace,"write_enabled":True,
            "sensitive_grants":sensitive},"persisted":{"agent_id":row[0],"workspace":row[1],"sensitivity":row[2]},"disposable":True}
    text=json.dumps(report,ensure_ascii=False,indent=2)
    if a.output:a.output.write_text(text+"\n")
    print(text)


if __name__=="__main__":main()
