import sys
import json,sqlite3
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from brain.control import ControlStore,IntegrationProfile,RegistryPolicy,READ_TOOLS,TOOLS
from brain.errors import BrainError

def profile(i="agent",enabled=False,ws=None,sensitive=None,tools=None):
 return IntegrationProfile(i,i,"custom_mcp","ssh_stdio","mini-core",enabled,ws or ["personal"],sensitive or [],tools or list(READ_TOOLS),i)

def test_lifecycle_fold_and_append_only_history(tmp_path):
 s=ControlStore(tmp_path/"control.db");s.save(profile());s.set_enabled("agent",True,reason="approved");s.revoke("agent",reason="done")
 assert s.get("agent").revoked and not s.get("agent").enabled
 h=s.history("agent");assert len(h)==3 and all(x["before_hash"]!=x["after_hash"] for x in h[1:])

def test_unknown_disabled_tool_workspace_sensitive_denials(tmp_path):
 s=ControlStore(tmp_path/"control.db");p=RegistryPolicy(s,"missing")
 with pytest.raises(BrainError,match="BRAIN_UNKNOWN_INTEGRATION"):p.authorize(["personal"])
 s.save(profile())
 with pytest.raises(BrainError,match="BRAIN_INTEGRATION_DISABLED"):RegistryPolicy(s,"agent").authorize(["personal"])
 q=profile(enabled=True,tools=["brain_health"]);s.save(q)
 with pytest.raises(BrainError,match="BRAIN_TOOL_DENIED"):RegistryPolicy(s,"agent").authorize(["personal"],tool="brain_search")
 q.allowed_tools=list(READ_TOOLS);s.save(q)
 with pytest.raises(BrainError,match="BRAIN_WORKSPACE_DENIED"):RegistryPolicy(s,"agent").authorize(["ai-pos"])
 with pytest.raises(BrainError,match="BRAIN_SENSITIVE_SCOPE_DENIED"):RegistryPolicy(s,"agent").authorize(["personal"],True)

def test_sensitive_must_be_subset_and_generic_requires_no_source_change(tmp_path):
 s=ControlStore(tmp_path/"control.db")
 with pytest.raises(ValueError):s.save(profile(sensitive=["sap-work"]))
 p=profile("future-agent",True,["personal"],[],["brain_search","brain_health"]);s.save(p)
 assert RegistryPolicy(s,"future-agent").authorize(["personal"]).integration_id=="future-agent"

def test_write_grant_defaults_off_and_instance_is_bound(tmp_path):
 s=ControlStore(tmp_path/"control.db");s.save(profile(enabled=True))
 with pytest.raises(BrainError,match="BRAIN_WRITE_DISABLED"):RegistryPolicy(s,"agent").authorize(["personal"],tool="brain_record_outcome")
 p=profile("writer",True,tools=list(TOOLS));p.write_enabled=True;p.brain_instance="personal";s.save(p)
 with pytest.raises(BrainError,match="BRAIN_INSTANCE_DENIED"):RegistryPolicy(s,"writer",instance_id="work").authorize(["personal"],tool="brain_record_outcome")
 with pytest.raises(BrainError,match="BRAIN_IDENTITY_MISMATCH"):RegistryPolicy(s,"writer",instance_id="personal",runtime_identity="impostor").authorize(["personal"],tool="brain_record_outcome")
 assert RegistryPolicy(s,"writer",instance_id="personal").resolve_scope(None,tool="brain_record_outcome")[0]==["personal"]
 s.set_write_grant("writer",False,reason="revoke")
 assert not s.get("writer").write_enabled and not (set(s.get("writer").allowed_tools)-set(READ_TOOLS))
 s.set_write_grant("writer",True,["brain_record_outcome"],reason="needed for handoff")
 assert s.get("writer").write_enabled and "brain_record_outcome" in s.get("writer").allowed_tools

def test_instance_workspace_mapping_and_work_sensitive_floor_are_enforced(tmp_path):
 s=ControlStore(tmp_path/"control.db")
 personal=profile("bad-personal",False,["sap-work"]);personal.brain_instance="personal"
 with pytest.raises(ValueError,match="Personal profiles"):s.save(personal)
 work=profile("bad-work",False,["personal"]);work.brain_instance="work";work.sensitive_workspace_grants=["personal"]
 with pytest.raises(ValueError,match="WORK profiles"):s.save(work)
 no_grant=profile("no-grant",False,["sap-work"]);no_grant.brain_instance="work"
 with pytest.raises(ValueError,match="matching sensitive grants"):s.save(no_grant)
 valid=profile("valid-work",False,["sap-work"],["sap-work"]);valid.brain_instance="work";s.save(valid)
 assert s.get("valid-work").sensitive_workspace_grants==["sap-work"]

def test_test_state_is_separate_from_real_use(tmp_path):
 s=ControlStore(tmp_path/"control.db");s.save(profile(enabled=True));s.mark_test("agent",True)
 p=s.get("agent");assert p.last_connection_test_status=="PASS" and p.last_successful_real_call is None

def test_persistence_across_store_restart(tmp_path):
 path=tmp_path/"control.db";ControlStore(path).save(profile(enabled=True));assert ControlStore(path).get("agent").enabled

def test_existing_registry_profiles_migrate_to_legacy_write_off(tmp_path):
 path=tmp_path/"old-control.db";con=sqlite3.connect(path)
 con.executescript("""CREATE TABLE integrations(
 integration_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, client_type TEXT NOT NULL,
 transport TEXT NOT NULL, host TEXT NOT NULL, enabled INTEGER NOT NULL, allowed_workspaces TEXT NOT NULL,
 sensitive_workspace_grants TEXT NOT NULL, allowed_tools TEXT NOT NULL, client_identity TEXT NOT NULL,
 authentication_mode TEXT NOT NULL, configuration_status TEXT NOT NULL, last_connection_test TEXT,
 last_connection_test_status TEXT,last_successful_real_call TEXT,last_error TEXT,created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);""")
 con.execute("INSERT INTO integrations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",("old","Old","custom_mcp","ssh_stdio","mini",1,json.dumps(["personal"]),"[]",json.dumps(list(READ_TOOLS)),"old","local_process","generated",None,None,None,None,"now","now",0));con.commit();con.close()
 migrated=ControlStore(path).get("old")
 assert migrated.brain_instance=="legacy" and not migrated.write_enabled and migrated.allowed_tools==list(READ_TOOLS)
