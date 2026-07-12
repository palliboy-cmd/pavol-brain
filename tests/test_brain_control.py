import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from brain.control import ControlStore,IntegrationProfile,RegistryPolicy,TOOLS
from brain.errors import BrainError

def profile(i="agent",enabled=False,ws=None,sensitive=None,tools=None):
 return IntegrationProfile(i,i,"custom_mcp","ssh_stdio","mini-core",enabled,ws or ["personal"],sensitive or [],tools or list(TOOLS),i)

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
 q.allowed_tools=list(TOOLS);s.save(q)
 with pytest.raises(BrainError,match="BRAIN_WORKSPACE_DENIED"):RegistryPolicy(s,"agent").authorize(["ai-pos"])
 with pytest.raises(BrainError,match="BRAIN_SENSITIVE_SCOPE_DENIED"):RegistryPolicy(s,"agent").authorize(["personal"],True)

def test_sensitive_must_be_subset_and_generic_requires_no_source_change(tmp_path):
 s=ControlStore(tmp_path/"control.db")
 with pytest.raises(ValueError):s.save(profile(sensitive=["sap-work"]))
 p=profile("future-agent",True,["personal"],[],["brain_search","brain_health"]);s.save(p)
 assert RegistryPolicy(s,"future-agent").authorize(["personal"]).integration_id=="future-agent"

def test_test_state_is_separate_from_real_use(tmp_path):
 s=ControlStore(tmp_path/"control.db");s.save(profile(enabled=True));s.mark_test("agent",True)
 p=s.get("agent");assert p.last_connection_test_status=="PASS" and p.last_successful_real_call is None

def test_persistence_across_store_restart(tmp_path):
 path=tmp_path/"control.db";ControlStore(path).save(profile(enabled=True));assert ControlStore(path).get("agent").enabled
