import json,sqlite3,sys,threading,urllib.error,urllib.parse,urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from brain.control import ControlStore,IntegrationProfile,READ_TOOLS,TOOLS
from brain.control_center import App,generated_config,handler,metrics,read_activity,serve
from brain import instance_identity

ROOT=Path(__file__).parents[1]

def stamped_journal(path,instance_id):
 con=sqlite3.connect(path);con.executescript((ROOT/"spike/schema/journal.sql").read_text())
 instance_identity.stamp_journal_marker(con,instance_id,"test-fixture-digest");con.commit();con.close()

class FakeBrain:
 def health(self):
  from brain.models import HealthReport
  return HealthReport(active_build_id="b",retrieval_db_available=True,journal_available=True,indexed_document_count=1,current_document_count=1,embedding_coverage=1,embedding_model="m",per_workspace_counts={},status="healthy")
 def rebuild_status(self):
  from brain.models import RebuildStatus
  return RebuildStatus(status="ready",active_build_id="b",last_known_build_metadata={})

def app(tmp_path):
 log=tmp_path/"audit.jsonl";log.write_text(json.dumps({"timestamp":"2026-07-12T00:00:00+00:00","client_identity":"agent","operation":"search","returned_record_ids":["rec-1"],"query":"PRIVATE RAW QUERY"})+"\nmalformed\n")
 return App(ControlStore(tmp_path/"control.db"),FakeBrain(),log,csrf="token")

def test_pages_render_without_query_or_record_content(tmp_path):
 a=app(tmp_path);a.store.save(IntegrationProfile("agent","Agent","custom_mcp","ssh_stdio","mini",True,["personal"],[],list(READ_TOOLS),"agent"))
 pages=[a.overview(),a.integrations(),a.add_form(),a.detail("agent"),a.policies(),a.activity(),a.runtime()]
 text="".join(pages);assert "PRIVATE RAW QUERY" not in text and "rec-1" in text and "brain_search" in text

def test_empty_malformed_audit_and_test_usage_separation(tmp_path):
 assert read_activity(tmp_path/"missing")==[]
 p=tmp_path/"a";p.write_text('bad\n'+json.dumps({"timestamp":"2026-07-12T00:00:00+00:00","operation":"search","test_call":True})+'\n')
 rows=read_activity(p);assert len(rows)==1 and metrics(rows)[0]=={"today":0,"7d":0,"28d":0}

def test_generated_config_is_profile_specific(tmp_path):
 for client in ("hermes","codex","claude","custom_mcp"):
  p=IntegrationProfile("future","Future",client,"ssh_stdio","mini",False,["personal"],[],list(READ_TOOLS),"future",brain_instance="personal")
  cfg=generated_config(p);assert "future" in cfg and "run_brain_mcp_ssh.sh" in cfg and "BRAIN_MCP_SSH_HOST" in cfg and "BRAIN_MCP_REMOTE_ROOT" in cfg and "PRIVATE" not in cfg

def test_add_forms_offer_only_instance_compatible_workspaces(tmp_path):
 text=app(tmp_path).add_form();personal,work=text.split("<h2>Work</h2>")
 assert "sap-work" not in personal
 assert "sap-work" in work and 'value="personal"' not in work and "ai-pos" not in work

def test_claude_config_targets_effective_user_scoped_cowork_configuration():
 p=IntegrationProfile("claude","Claude","claude","ssh_stdio","mini",True,["ai-pos"],[],list(READ_TOOLS),"claude",brain_instance="personal")
 cfg=generated_config(p)
 assert "claude mcp add -s user" in cfg and '"type": "stdio"' in cfg and "BRAIN_INTEGRATION_ID=claude" in cfg and "BRAIN_INSTANCE=personal" in cfg and "BRAIN_MCP_REMOTE_ROOT" in cfg

def test_generated_cli_configuration_quotes_paths_with_spaces(monkeypatch):
 monkeypatch.setenv("BRAIN_MCP_REMOTE_ROOT","/tmp/remote root")
 monkeypatch.setenv("BRAIN_MCP_CLIENT_LAUNCHER","/tmp/client launcher")
 p=IntegrationProfile("claude","Claude","claude","ssh_stdio","mini",True,["personal"],[],list(READ_TOOLS),"claude",brain_instance="personal")
 cfg=generated_config(p)
 assert "'BRAIN_MCP_REMOTE_ROOT=/tmp/remote root'" in cfg and "'/tmp/client launcher'" in cfg

def test_csrf_post_only_and_lifecycle(tmp_path,monkeypatch):
 monkeypatch.setenv("BRAIN_PERSONAL_JOURNAL_DB",str(tmp_path/"personal.db"));stamped_journal(tmp_path/"personal.db","personal")
 a=app(tmp_path);srv=ThreadingHTTPServer(("127.0.0.1",0),handler(a));threading.Thread(target=srv.serve_forever,daemon=True).start();base=f"http://127.0.0.1:{srv.server_port}"
 try:
  with pytest.raises(urllib.error.HTTPError) as e:urllib.request.urlopen(urllib.request.Request(base+"/integrations/add",data=b"integration_id=x",method="POST"));assert e.value.code==403
  data=urllib.parse.urlencode({"csrf":"token","confirm":"on","integration_id":"new-agent","display_name":"New","client_type":"custom_mcp","transport":"ssh_stdio","host":"mini","allowed_workspaces":"personal","allowed_tools":"brain_search"}).encode()
  urllib.request.urlopen(urllib.request.Request(base+"/integrations/add",data=data,method="POST"));assert a.store.get("new-agent") and not a.store.get("new-agent").enabled
  grant=urllib.parse.urlencode({"csrf":"token","confirm":"on","write_enabled":"on","write_tools":"brain_record_outcome","reason":"handoff outcomes"}).encode()
  urllib.request.urlopen(urllib.request.Request(base+"/integrations/new-agent/write",data=grant,method="POST"));assert a.store.get("new-agent").write_enabled
  assert urllib.request.urlopen(base+"/integrations").status==200
 finally:srv.shutdown()

def test_non_loopback_bind_rejected():
 with pytest.raises(ValueError):serve("0.0.0.0",8765)
