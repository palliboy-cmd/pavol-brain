"""Loopback-only server-rendered Brain Control Center."""
import html
import json
import os
import asyncio
import hashlib
import secrets
import sqlite3
import sys
from datetime import datetime,timezone,timedelta
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse

from .api import Brain
from .config import BrainConfig
from .control import CLIENT_TYPES,TOOLS,TRANSPORTS,ControlStore,IntegrationProfile

def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def connection_test(profile,config,store):
    """Real stdio MCP test with a fixed profile identity and read-only hash gate."""
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    root=Path(__file__).parents[1];before=(_sha(config.journal_db_path),_sha(config.retrieval_db_path))
    env={**os.environ,"BRAIN_CONTROL_DB":str(store.path),"BRAIN_INTEGRATION_ID":profile.integration_id,
         "BRAIN_CLIENT_IDENTITY":profile.client_identity,"BRAIN_AUDIT_TEST_CALL":"true",
         "BRAIN_JOURNAL_DB":str(config.journal_db_path),"BRAIN_RETRIEVAL_DB":str(config.retrieval_db_path),
         "BRAIN_AUDIT_LOG":str(config.audit_log_path or Path.home()/"Library/Logs/Pavol-Brain/audit.jsonl")}
    params=StdioServerParameters(command=sys.executable,args=[str(root/"scripts/run_brain_mcp.py")],env=env)
    async def run():
      async with stdio_client(params) as (read,write):
       async with ClientSession(read,write) as session:
        await session.initialize();tools=(await session.list_tools()).tools
        if [x.name for x in tools]!=list(TOOLS):raise RuntimeError("tool list mismatch")
        health=json.loads((await session.call_tool("brain_health",{})).content[0].text)
        if "error" in health:raise RuntimeError(health["error"]["code"])
        search=None
        if "brain_search" in profile.allowed_tools and profile.allowed_workspaces:
            search=json.loads((await session.call_tool("brain_search",{"query":"control center connection validation","workspaces":[profile.allowed_workspaces[0]],"limit":1,"request_id":"connection-test"})).content[0].text)
            if "error" in search:raise RuntimeError(search["error"]["code"])
            if search.get("results") and not search["results"][0].get("provenance",{}).get("source_event_id"):raise RuntimeError("provenance missing")
            ungranted=next((x for x in ("ai-pos","personal","sap-work") if x not in profile.allowed_workspaces),None)
            if ungranted:
                denied=json.loads((await session.call_tool("brain_search",{"query":"denial validation","workspaces":[ungranted]})).content[0].text)
                if denied.get("error",{}).get("code")!="BRAIN_WORKSPACE_DENIED":raise RuntimeError("ungranted workspace was not denied")
            if "sap-work" not in profile.sensitive_workspace_grants:
                denied=json.loads((await session.call_tool("brain_search",{"query":"sensitive denial validation","workspaces":["sap-work"],"sensitive_allowed":True})).content[0].text)
                if denied.get("error",{}).get("code") not in {"BRAIN_WORKSPACE_DENIED","BRAIN_SENSITIVE_SCOPE_DENIED"}:raise RuntimeError("sensitive workspace was not denied")
        return {"health":health.get("status"),"result_ids":[x["record_id"] for x in (search or {}).get("results",[])]}
    result=asyncio.run(run());after=(_sha(config.journal_db_path),_sha(config.retrieval_db_path))
    if before!=after:raise RuntimeError("brain database mutation detected")
    return result

def esc(x):return html.escape(str(x if x is not None else ""))
def read_activity(path,limit=200):
    out=[]
    if not path or not Path(path).exists():return out
    for line in Path(path).read_text(errors="replace").splitlines()[-limit:]:
        try:
            row=json.loads(line)
            if isinstance(row,dict):out.append(row)
        except (json.JSONDecodeError,TypeError):continue
    return list(reversed(out))
def metrics(rows):
    now=datetime.now(timezone.utc);counts={"today":0,"7d":0,"28d":0};last=None
    for r in rows:
        if r.get("operation") not in TOOLS and r.get("operation") not in {"search","get_record","get_related","health","rebuild_status"}:continue
        if r.get("test_call"):continue
        try:ts=datetime.fromisoformat(r["timestamp"].replace("Z","+00:00"))
        except (KeyError,ValueError):continue
        age=now-ts
        if age<timedelta(days=1):counts["today"]+=1
        if age<timedelta(days=7):counts["7d"]+=1
        if age<timedelta(days=28):counts["28d"]+=1
        last=last or r.get("timestamp")
    return counts,last

class App:
    def __init__(self,store,brain,audit_path,csrf=None,config=None):self.store,self.brain,self.audit_path,self.csrf,self.config=store,brain,Path(audit_path),csrf or secrets.token_urlsafe(32),config
    def layout(self,title,body):
        nav=' '.join(f'<a href="{p}">{n}</a>' for p,n in [("/","Overview"),("/integrations","Integrations"),("/integrations/add","Add integration"),("/policies","Access policies"),("/activity","Activity"),("/runtime","Runtime")])
        return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{esc(title)}</title><style>body{{font:15px system-ui;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#17202a}}nav a{{margin-right:1rem}}table{{border-collapse:collapse;width:100%;margin:1rem 0}}th,td{{border:1px solid #ccd;padding:.45rem;text-align:left;vertical-align:top}}.ok{{color:#176b36}}.bad{{color:#a11}}code,pre{{background:#f3f5f7;padding:.2rem;white-space:pre-wrap}}fieldset{{margin:1rem 0}}button{{margin:.2rem}}.muted{{color:#667}}</style></head><body><h1>{esc(title)}</h1><nav>{nav}</nav>{body}</body></html>'''
    def overview(self):
        h=self.brain.health();rows=read_activity(self.audit_path);counts,last=metrics(rows);fail=[x for x in rows if x.get("error_code")][:5]
        body=f'''<p class="{'ok' if h.status=='healthy' else 'bad'}">Brain: {esc(h.status)} · index current: {not h.index_behind} · stale: {h.stale_index}</p><table><tr><th>Journal head</th><td>{esc(h.journal_head_cursor)}</td></tr><tr><th>Projector cursor</th><td>{esc(h.retrieval_cursor)}</td></tr><tr><th>Documents</th><td>{h.indexed_document_count}</td></tr><tr><th>Embedding coverage</th><td>{h.embedding_coverage}</td></tr><tr><th>Endpoint</th><td>{esc(h.embedding_endpoint_status)}</td></tr><tr><th>Last projector</th><td>{esc(h.last_successful_projector_run)}</td></tr><tr><th>Last agent use</th><td>{esc(last)}</td></tr><tr><th>Calls today / 7d / 28d</th><td>{counts['today']} / {counts['7d']} / {counts['28d']}</td></tr></table><h2>Recent failures</h2>{self.activity_table(fail)}'''
        return self.layout("Pavol-Brain Control Center",body)
    def integrations(self):
        rows=read_activity(self.audit_path);used={r.get("client_identity") for r in rows if not r.get("test_call")}
        trs=[]
        for p in self.store.list():trs.append(f'<tr><td><a href="/integrations/{esc(p.integration_id)}">{esc(p.display_name)}</a></td><td>{esc(p.client_type)}</td><td>{"enabled" if p.enabled else "disabled"}</td><td>{esc(p.configuration_status)}</td><td>{"used" if p.client_identity in used or p.last_successful_real_call else "never used"}</td><td>{esc(p.transport)}</td><td>{len(p.allowed_workspaces)}</td><td>{"yes" if p.sensitive_workspace_grants else "no"}</td><td>{esc(p.last_connection_test_status)}</td></tr>')
        return self.layout("Integrations",'<table><tr><th>Name</th><th>Client</th><th>Access</th><th>Configured</th><th>Actual use</th><th>Transport</th><th>Workspaces</th><th>Sensitive</th><th>Test</th></tr>'+''.join(trs)+'</table>')
    def add_form(self):
        checks=lambda name,items:''.join(f'<label><input type="checkbox" name="{name}" value="{esc(x)}"> {esc(x)}</label> ' for x in items)
        body=f'''<form method="post" action="/integrations/add"><input type="hidden" name="csrf" value="{self.csrf}"><label>ID <input name="integration_id" required pattern="[a-z0-9][a-z0-9-]*"></label><br><label>Name <input name="display_name" required></label><br><label>Client <select name="client_type">{''.join(f'<option>{x}</option>' for x in CLIENT_TYPES)}</select></label><br><label>Transport <select name="transport">{''.join(f'<option>{x}</option>' for x in TRANSPORTS)}</select></label><br><label>Host <input name="host" value="mini-core"></label><fieldset><legend>Allowed workspaces</legend>{checks('allowed_workspaces',['ai-pos','personal','ai-pos-app','smart-timesheet','abap-object-exporter','sap-work'])}</fieldset><fieldset><legend>Sensitive grants</legend>{checks('sensitive_grants',['sap-work'])}</fieldset><fieldset><legend>Allowed tools</legend>{checks('allowed_tools',TOOLS)}</fieldset><label>Reason <input name="reason"></label><p><label><input type="checkbox" name="confirm"> Confirm profile creation</label></p><button>Create disabled profile</button></form>'''
        return self.layout("Add integration",body)
    def detail(self,i):
        p=self.store.get(i)
        if not p:return self.layout("Not found","<p>Unknown integration.</p>")
        cfg=generated_config(p);hist=self.store.history(i);activity=[x for x in read_activity(self.audit_path) if x.get("client_identity")==p.client_identity][:20]
        actions=''.join(f'<form method="post" action="/integrations/{esc(i)}/{a}" style="display:inline"><input type="hidden" name="csrf" value="{self.csrf}"><input type="hidden" name="confirm" value="yes"><input name="reason" placeholder="reason"><button>{a}</button></form>' for a in (["disable"] if p.enabled else ["enable"])+["test","revoke"])
        body=f'<p>{actions}</p><pre>{esc(json.dumps({**p.__dict__,"generated_configuration":None},indent=2))}</pre><h2>Generated configuration</h2><pre>{esc(cfg)}</pre><h2>Recent activity</h2>{self.activity_table(activity)}<h2>Policy history</h2><table><tr><th>Time</th><th>Actor</th><th>Before</th><th>After</th><th>Fields</th><th>Reason</th></tr>'+''.join(f'<tr><td>{esc(x["occurred_at"])}</td><td>{esc(x["actor"])}</td><td>{esc(x["before_hash"])}</td><td>{esc(x["after_hash"])}</td><td>{esc(x["changed_fields"])}</td><td>{esc(x["reason"])}</td></tr>' for x in hist)+'</table>'
        return self.layout(p.display_name,body)
    def policies(self):
        rows=[]
        for p in self.store.list():
            for tool in p.allowed_tools:
                for ws in p.allowed_workspaces:rows.append(f'<tr><td>{esc(p.display_name)}</td><td>{esc(tool)}</td><td>{esc(ws)}</td><td>{"yes" if ws in p.sensitive_workspace_grants else "no"}</td><td>{"allow" if p.enabled else "disabled"}</td></tr>')
        return self.layout("Access policies",'<table><tr><th>Agent</th><th>Tool</th><th>Workspace</th><th>Sensitive</th><th>Effect</th></tr>'+''.join(rows)+'</table>')
    def activity_table(self,rows):
        return '<table><tr><th>Time</th><th>Integration</th><th>Operation</th><th>Scope</th><th>Results</th><th>IDs</th><th>Latency</th><th>Error</th><th>Build</th><th>Stale</th></tr>'+''.join(f'<tr><td>{esc(x.get("timestamp"))}</td><td>{esc(x.get("client_identity"))}</td><td>{esc(x.get("operation"))}</td><td>{esc(x.get("resolved_workspaces"))}</td><td>{esc(x.get("result_count"))}</td><td>{esc(x.get("returned_record_ids"))}</td><td>{esc(x.get("total_latency_ms"))}</td><td>{esc(x.get("error_code"))}</td><td>{esc(x.get("active_build_id"))}</td><td>{esc(x.get("stale_flag"))}</td></tr>' for x in rows)+'</table>'
    def activity(self):return self.layout("Activity",self.activity_table(read_activity(self.audit_path)))
    def runtime(self):
        h=self.brain.health();r=self.brain.rebuild_status();return self.layout("Runtime",f'<h2>Health</h2><pre>{esc(h.model_dump_json(indent=2))}</pre><h2>Projector/build</h2><pre>{esc(r.model_dump_json(indent=2))}</pre><p>LaunchAgent cadence: 300 seconds. Runtime state, locks, logs and backups are outside Git.</p>')

def generated_config(p):
    root="/Users/pavol/Documents/Personal/Projects/pavol-brain";cmd=f"{root}/scripts/run_brain_mcp_ssh.sh";env={"BRAIN_INTEGRATION_ID":p.integration_id}
    if p.client_type=="hermes":return f"hermes mcp add {p.integration_id} --command {cmd} --env BRAIN_INTEGRATION_ID={p.integration_id}"
    if p.client_type=="codex":return f"codex mcp add {p.integration_id} --env BRAIN_INTEGRATION_ID={p.integration_id} -- {cmd}"
    if p.client_type=="claude":return f"claude mcp add -s user {p.integration_id} -e BRAIN_INTEGRATION_ID={p.integration_id} -- {cmd}\n\n"+json.dumps({"mcpServers":{p.integration_id:{"type":"stdio","command":cmd,"args":[],"env":env}}},indent=2)
    return json.dumps({"mcpServers":{p.integration_id:{"command":cmd,"args":[],"env":env}}},indent=2)

def handler(app):
 class Handler(BaseHTTPRequestHandler):
    def send(self,body,status=200,location=None):
        self.send_response(status);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Security-Policy","default-src 'self'; style-src 'unsafe-inline'");self.send_header("X-Frame-Options","DENY");
        if location:self.send_header("Location",location)
        data=body.encode();self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/":body=app.overview()
        elif path=="/integrations":body=app.integrations()
        elif path=="/integrations/add":body=app.add_form()
        elif path=="/policies":body=app.policies()
        elif path=="/activity":body=app.activity()
        elif path=="/runtime":body=app.runtime()
        elif path.startswith("/integrations/"):body=app.detail(path.split("/")[2])
        else:return self.send("not found",404)
        self.send(body)
    def do_POST(self):
        length=min(int(self.headers.get("Content-Length","0")),65536);form={k:v for k,v in parse_qs(self.rfile.read(length).decode()).items()}
        one=lambda k,d="":form.get(k,[d])[0]
        if not secrets.compare_digest(one("csrf"),app.csrf):return self.send("CSRF validation failed",403)
        path=urlparse(self.path).path
        if path=="/integrations/add":
            if one("confirm")!="on":return self.send("explicit confirmation required",400)
            try:p=IntegrationProfile(one("integration_id"),one("display_name"),one("client_type"),one("transport"),one("host"),False,form.get("allowed_workspaces",[]),form.get("sensitive_grants",[]),form.get("allowed_tools",[]),one("integration_id"));app.store.save(p,reason=one("reason"))
            except (ValueError,sqlite3.Error) as e:return self.send(esc(e),400)
            return self.send("",303,"/integrations/"+p.integration_id)
        parts=path.strip("/").split("/")
        if len(parts)==3 and parts[0]=="integrations":
            i,action=parts[1],parts[2]
            if one("confirm")!="yes":return self.send("explicit confirmation required",400)
            try:
                if action=="enable":app.store.set_enabled(i,True,reason=one("reason"))
                elif action=="disable":app.store.set_enabled(i,False,reason=one("reason"))
                elif action=="revoke":app.store.revoke(i,reason=one("reason"))
                elif action=="test":
                    try:connection_test(app.store.get(i),app.config,app.store);app.store.mark_test(i,True)
                    except Exception as exc:app.store.mark_test(i,False,type(exc).__name__);return self.send("connection test failed: "+esc(type(exc).__name__),400)
                else:return self.send("not found",404)
            except KeyError:return self.send("not found",404)
            return self.send("",303,"/integrations/"+i)
        self.send("not found",404)
    def log_message(self,fmt,*args):sys.stderr.write((fmt%args)+"\n")
 return Handler

def serve(host="127.0.0.1",port=8765):
    if host not in {"127.0.0.1","::1","localhost"}:raise ValueError("Control Center must bind to loopback")
    config=BrainConfig();store=ControlStore(os.environ.get("BRAIN_CONTROL_DB",str(Path.home()/"Library/Application Support/Pavol-Brain/brain-control.db")))
    app=App(store,Brain(config),config.audit_log_path or Path.home()/"Library/Logs/Pavol-Brain/audit.jsonl",config=config)
    ThreadingHTTPServer((host,port),handler(app)).serve_forever()
