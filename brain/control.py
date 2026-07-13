"""Operational integration registry; separate from canonical memory truth."""
import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

READ_TOOLS = ("brain_search","brain_get_record","brain_get_related","brain_health","brain_rebuild_status")
WRITE_TOOLS = ("brain_record_outcome","brain_record_decision")
TOOLS = READ_TOOLS + WRITE_TOOLS
PERSONAL_WORKSPACES = frozenset({"abap-object-exporter","ai-pos","ai-pos-app","personal","smart-timesheet"})
WORK_WORKSPACES = frozenset({"sap-work"})
SENSITIVE_WORKSPACES = WORK_WORKSPACES
CLIENT_TYPES = ("hermes","codex","claude","custom_mcp")
TRANSPORTS = ("local_stdio","ssh_stdio","unavailable","not_configured")

SCHEMA = """
CREATE TABLE IF NOT EXISTS control_schema(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS integration_events(
 event_id TEXT PRIMARY KEY, integration_id TEXT NOT NULL, event_type TEXT NOT NULL,
 occurred_at TEXT NOT NULL, actor TEXT NOT NULL, before_hash TEXT, after_hash TEXT,
 changed_fields TEXT NOT NULL, reason TEXT, snapshot TEXT);
CREATE INDEX IF NOT EXISTS integration_events_by_profile ON integration_events(integration_id,occurred_at,event_id);
CREATE TABLE IF NOT EXISTS integrations(
 integration_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, client_type TEXT NOT NULL,
 transport TEXT NOT NULL, host TEXT NOT NULL, enabled INTEGER NOT NULL,
 allowed_workspaces TEXT NOT NULL, sensitive_workspace_grants TEXT NOT NULL,
 allowed_tools TEXT NOT NULL, client_identity TEXT NOT NULL, authentication_mode TEXT NOT NULL,
 configuration_status TEXT NOT NULL, last_connection_test TEXT, last_connection_test_status TEXT,
 last_successful_real_call TEXT, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 revoked INTEGER NOT NULL DEFAULT 0, write_enabled INTEGER NOT NULL DEFAULT 0,
 brain_instance TEXT NOT NULL DEFAULT 'legacy');
"""

def now(): return datetime.now(timezone.utc).isoformat()
def policy_hash(data):
    keys=("enabled","allowed_workspaces","sensitive_workspace_grants","allowed_tools","revoked","write_enabled","brain_instance")
    return hashlib.sha256(json.dumps({k:data.get(k) for k in keys},sort_keys=True).encode()).hexdigest()

@dataclass
class IntegrationProfile:
    integration_id: str; display_name: str; client_type: str; transport: str; host: str
    enabled: bool; allowed_workspaces: list[str]; sensitive_workspace_grants: list[str]
    allowed_tools: list[str]; client_identity: str; authentication_mode: str = "local_process"
    configuration_status: str = "generated"; last_connection_test: str | None = None
    last_connection_test_status: str | None = None; last_successful_real_call: str | None = None
    last_error: str | None = None; created_at: str = ""; updated_at: str = ""; revoked: bool = False
    write_enabled: bool = False; brain_instance: str = "legacy"

class ControlStore:
    def __init__(self,path): self.path=Path(path)
    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True); con=sqlite3.connect(self.path)
        con.row_factory=sqlite3.Row; con.execute("PRAGMA foreign_keys=ON"); con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        columns={row[1] for row in con.execute("PRAGMA table_info(integrations)")}
        if "write_enabled" not in columns:con.execute("ALTER TABLE integrations ADD COLUMN write_enabled INTEGER NOT NULL DEFAULT 0")
        if "brain_instance" not in columns:con.execute("ALTER TABLE integrations ADD COLUMN brain_instance TEXT NOT NULL DEFAULT 'legacy'")
        con.execute("INSERT OR IGNORE INTO control_schema VALUES(2,?)",(now(),)); con.commit(); return con
    def _decode(self,row):
        if not row:return None
        d=dict(row)
        for k in ("allowed_workspaces","sensitive_workspace_grants","allowed_tools"):d[k]=json.loads(d[k])
        d["enabled"]=bool(d["enabled"]);d["revoked"]=bool(d["revoked"]);d["write_enabled"]=bool(d["write_enabled"]);return IntegrationProfile(**d)
    def get(self,integration_id):
        con=self.connect();return self._decode(con.execute("SELECT * FROM integrations WHERE integration_id=?",(integration_id,)).fetchone())
    def list(self,include_revoked=False):
        con=self.connect();sql="SELECT * FROM integrations"+("" if include_revoked else " WHERE revoked=0")+" ORDER BY display_name"
        return [self._decode(r) for r in con.execute(sql)]
    def save(self,profile,actor="Pavol/operator",reason=None):
        if profile.client_type not in CLIENT_TYPES or profile.transport not in TRANSPORTS:raise ValueError("invalid client type or transport")
        if not set(profile.sensitive_workspace_grants)<=set(profile.allowed_workspaces):raise ValueError("sensitive grants must be allowed workspaces")
        if not set(profile.allowed_tools)<=set(TOOLS):raise ValueError("unknown tool")
        if set(profile.allowed_tools)&set(WRITE_TOOLS) and not profile.write_enabled:raise ValueError("write tools require an explicit write grant")
        if profile.brain_instance not in {"legacy","personal","work"}:raise ValueError("invalid brain instance")
        allowed=set(profile.allowed_workspaces);grants=set(profile.sensitive_workspace_grants)
        if profile.brain_instance=="personal" and not allowed<=PERSONAL_WORKSPACES:raise ValueError("Personal profiles may only use Personal workspaces")
        if profile.brain_instance=="work" and (not allowed<=WORK_WORKSPACES or not allowed<=grants):raise ValueError("WORK profiles require WORK workspaces and matching sensitive grants")
        if profile.brain_instance=="legacy" and (profile.write_enabled or set(profile.allowed_tools)&set(WRITE_TOOLS)):raise ValueError("legacy profiles are read-only")
        con=self.connect();old=self.get(profile.integration_id); stamp=now()
        profile.created_at=old.created_at if old else stamp;profile.updated_at=stamp
        data=asdict(profile);before=policy_hash(asdict(old)) if old else None;after=policy_hash(data)
        changed=sorted(k for k,v in data.items() if not old or asdict(old).get(k)!=v)
        values={**data,"enabled":int(profile.enabled),"revoked":int(profile.revoked),"write_enabled":int(profile.write_enabled),**{k:json.dumps(data[k],sort_keys=True) for k in ("allowed_workspaces","sensitive_workspace_grants","allowed_tools")}}
        columns=list(values); marks=",".join("?"*len(columns)); updates=",".join(f"{x}=excluded.{x}" for x in columns if x not in ("integration_id","created_at"))
        try:
            con.execute("BEGIN IMMEDIATE");con.execute(f"INSERT INTO integrations({','.join(columns)}) VALUES({marks}) ON CONFLICT(integration_id) DO UPDATE SET {updates}",[values[x] for x in columns])
            con.execute("INSERT INTO integration_events VALUES(?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),profile.integration_id,"created" if not old else "updated",stamp,actor,before,after,json.dumps(changed),reason,json.dumps(data,sort_keys=True)))
            con.commit()
        except: con.rollback();raise
        return profile
    def set_enabled(self,integration_id,enabled,actor="Pavol/operator",reason=None):
        p=self.get(integration_id)
        if not p:raise KeyError(integration_id)
        p.enabled=enabled;return self.save(p,actor,reason)
    def revoke(self,integration_id,actor="Pavol/operator",reason=None):
        p=self.get(integration_id)
        if not p:raise KeyError(integration_id)
        p.enabled=False;p.revoked=True;return self.save(p,actor,reason)
    def set_write_grant(self,integration_id,enabled,tools=(),actor="Pavol/operator",reason=None):
        p=self.get(integration_id)
        if not p:raise KeyError(integration_id)
        selected=set(tools)
        if not selected<=set(WRITE_TOOLS):raise ValueError("unknown write tool")
        p.allowed_tools=[tool for tool in p.allowed_tools if tool not in WRITE_TOOLS]
        p.write_enabled=bool(enabled)
        if p.write_enabled:p.allowed_tools.extend(tool for tool in WRITE_TOOLS if tool in selected)
        return self.save(p,actor,reason)
    def history(self,integration_id=None):
        con=self.connect();q="SELECT * FROM integration_events";args=[]
        if integration_id:q+=" WHERE integration_id=?";args=[integration_id]
        return [dict(r) for r in con.execute(q+" ORDER BY occurred_at DESC,event_id DESC",args)]
    def mark_test(self,integration_id,success,error=None):
        p=self.get(integration_id);p.last_connection_test=now();p.last_connection_test_status="PASS" if success else "FAIL";p.last_error=error;return self.save(p,actor="connection-test",reason="automated connection test")
    def mark_real_call(self,integration_id):
        con=self.connect();con.execute("UPDATE integrations SET last_successful_real_call=?,updated_at=? WHERE integration_id=?",(now(),now(),integration_id));con.commit()

class RegistryPolicy:
    def __init__(self,store,integration_id,audit=None,instance_id="legacy",runtime_identity=None):self.store,self.integration_id,self.audit,self.instance_id,self.runtime_identity=store,integration_id,audit,instance_id,runtime_identity
    @property
    def profile(self):return self.integration_id
    def _current(self,tool,request_id=""):
        p=self.store.get(self.integration_id)
        if not p or p.revoked:raise self._deny("BRAIN_UNKNOWN_INTEGRATION","unknown or revoked integration",request_id)
        if not p.enabled:raise self._deny("BRAIN_INTEGRATION_DISABLED","integration is disabled",request_id)
        if p.brain_instance != self.instance_id:raise self._deny("BRAIN_INSTANCE_DENIED","profile is bound to a different Brain instance",request_id)
        if self.runtime_identity is not None and p.client_identity != self.runtime_identity:raise self._deny("BRAIN_IDENTITY_MISMATCH","launcher identity does not match the profile",request_id)
        if tool in WRITE_TOOLS and not p.write_enabled:raise self._deny("BRAIN_WRITE_DISABLED","write access is disabled for this profile",request_id)
        if tool not in p.allowed_tools:raise self._deny("BRAIN_TOOL_DENIED","tool is not granted",request_id)
        return p
    def _deny(self,code,message,request_id,details=None):
        from .errors import BrainError
        if self.audit:self.audit.write("policy_denial",request_id=request_id,error_code=code,integration_id=self.integration_id)
        return BrainError(code,message,request_id,details or {})
    def authorize(self,requested=(),sensitive_allowed=False,request_id="",tool="brain_search"):
        p=self._current(tool,request_id);requested=set(requested)
        denied=requested-set(p.allowed_workspaces)
        if denied:raise self._deny("BRAIN_WORKSPACE_DENIED","workspace is not granted",request_id,{"workspaces":sorted(denied)})
        if sensitive_allowed and not requested<=set(p.sensitive_workspace_grants):raise self._deny("BRAIN_SENSITIVE_SCOPE_DENIED","sensitive scope is not granted",request_id,{"workspaces":sorted(requested-set(p.sensitive_workspace_grants))})
        return p
    def resolve_scope(self,requested=None,request_id="",tool="brain_search"):
        p=self._current(tool,request_id);scope=set(p.allowed_workspaces) if requested is None else set(requested)
        denied=scope-set(p.allowed_workspaces)
        if denied:raise self._deny("BRAIN_WORKSPACE_DENIED","workspace is not granted",request_id,{"workspaces":sorted(denied)})
        if not scope:raise self._deny("BRAIN_WORKSPACE_REQUIRED","profile has no default workspace scope",request_id)
        return sorted(scope),p
    def mark_real_call(self):self.store.mark_real_call(self.integration_id)
