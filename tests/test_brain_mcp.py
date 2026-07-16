import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))

from brain.api import Brain
from brain.config import BrainConfig
from brain.control import ControlStore, IntegrationProfile, READ_TOOLS, RegistryPolicy
from brain.mcp_server import CapabilityPolicy, create_server
from brain.models import SearchRequest
from test_brain_contract import FixtureRepository, FixtureTransport, REPORT

CANARY = "api_key=sk-live-fakeFAKE1234567890fake"


def server(allowed=("ai-pos", "personal")):
    repo = FixtureRepository(); vectors = {q: [1.0 if i == n else 0.0 for n in range(len(repo.keys))] for i, q in enumerate(repo.keys)}
    brain = Brain(BrainConfig(embedding_dimension=len(repo.keys)), FixtureTransport(vectors), repo)
    return create_server(policy=CapabilityPolicy(frozenset(allowed)), brain=brain)


def call(mcp, name, arguments):
    content = asyncio.run(mcp.call_tool(name, arguments))
    return json.loads(content[0].text)


def test_exact_tool_list_and_search_schema_parity():
    tools = asyncio.run(server().list_tools())
    assert [x.name for x in tools] == ["brain_search","brain_get_record","brain_get_related","brain_health","brain_rebuild_status","brain_record_outcome","brain_record_decision"]
    actual = tools[0].inputSchema; canonical = SearchRequest.model_json_schema()
    assert actual["properties"] == canonical["properties"] and actual["required"] == canonical["required"]
    assert not any(word in x.name for x in tools for word in ("delete","project","approve","shell","remember"))


def test_search_success_request_id_and_provenance():
    q = REPORT["queries"][0]
    result = call(server(), "brain_search", {"query":q["query"],"workspaces":q["scope"],"types":q["filters"]["types"],"request_id":"mcp-test"})
    assert result["request_id"] == "mcp-test" and result["results"]
    assert all(x["provenance"]["source_event_id"] and x["retrieval_build_id"] for x in result["results"])


def test_validation_feature_and_workspace_denials_are_structured():
    mcp = server()
    missing = call(mcp, "brain_search", {"query":"x","workspaces":[]})
    assert missing["error"]["code"] == "BRAIN_WORKSPACE_REQUIRED"
    disabled = call(mcp, "brain_search", {"query":"x","workspaces":["ai-pos"],"min_score":0.2})
    assert disabled["error"]["code"] == "BRAIN_FEATURE_NOT_ENABLED"
    denied = call(mcp, "brain_search", {"query":"x","workspaces":["sap-work"],"sensitive_allowed":True})
    assert denied["error"]["code"] == "BRAIN_WORKSPACE_DENIED"
    assert denied["error"]["request_id"].startswith("uuid4-compat:")
    supplied = call(mcp, "brain_search", {"query":"x","workspaces":["sap-work"],"request_id":"caller-denial-id"})
    assert supplied["error"]["request_id"] == "caller-denial-id"


def test_record_related_health_and_status():
    mcp = server()
    assert call(mcp,"brain_get_record",{"record_id":"rec-001"})["record_id"] == "rec-001"
    assert call(mcp,"brain_get_related",{"record_id":"rec-001"})["related"]
    assert "status" in call(mcp,"brain_health",{})
    assert call(mcp,"brain_rebuild_status",{})["status"] in {"ready","rebuild_required","failed"}


def test_b7_probe_rerun_invalid_request_id_rejected_before_policy_denial_audit(tmp_path):
    """B7 baseline probe re-run: request_id was a free string, unscanned, and
    written verbatim to the audit log by every operation -- including the
    RegistryPolicy policy_denial audit event that fires *before* brain.search
    is ever called. Assert the canary never reaches that audit line."""
    audit_log = tmp_path / "audit.jsonl"
    repo = FixtureRepository(); vectors = {q: [1.0 if i == n else 0.0 for n in range(len(repo.keys))] for i, q in enumerate(repo.keys)}
    fixture_brain = Brain(BrainConfig(embedding_dimension=len(repo.keys), audit_log_path=audit_log, instance_id="legacy"),
                          FixtureTransport(vectors), repo)
    store = ControlStore(tmp_path / "control.db")
    profile = IntegrationProfile("agent-x", "agent-x", "custom_mcp", "local_stdio", "local", True,
                                  ["ai-pos"], [], list(READ_TOOLS), "agent-x", brain_instance="legacy")
    store.save(profile, reason="b7 probe fixture")
    policy = RegistryPolicy(store, "agent-x", fixture_brain.audit, instance_id="legacy", runtime_identity="agent-x")
    mcp = create_server(policy=policy, brain=fixture_brain)

    # sap-work is not in the profile's allowed_workspaces, so without the fix
    # this would raise BRAIN_WORKSPACE_DENIED and write a policy_denial audit
    # line carrying the raw (canary) request_id before brain.search runs.
    result = call(mcp, "brain_search", {"query": "x", "workspaces": ["sap-work"], "request_id": CANARY})
    assert result["error"]["code"] == "BRAIN_INVALID_REQUEST"
    assert result["error"]["request_id"] == ""
    assert CANARY not in json.dumps(result) and "sk-live" not in json.dumps(result)
    audit_bytes = audit_log.read_bytes() if audit_log.exists() else b""
    assert CANARY.encode() not in audit_bytes and b"sk-live" not in audit_bytes


def test_f2b_probe_mcp_verification_secret_key_nonstring_value_no_pre_body_leak(tmp_path):
    """F2b reproducer, through the real MCP tool boundary (not just the
    domain layer): brain_record_outcome's raw FastMCP signature used to type
    verification as dict[str, str]. A secret-shaped key paired with a
    non-string value failed FastMCP's own pre-body pydantic validation --
    built from the raw tool signature, running before this codebase's tool
    body and its Band C sanitization ever execute -- and raised a ToolError
    whose message embedded the raw key twice (loc path + input_value).
    verification's raw-boundary value type is now Any, so no pre-body nested
    validation runs; the domain model (OutcomeRequest.verification,
    brain/models.py) still enforces dict[VerificationKey, str], so the
    non-string value is rejected inside the tool body through the same
    sanitized-error path (F1) as every other write validation failure."""
    from test_brain_write import brain_with_audit
    b, journal, audit_log = brain_with_audit(tmp_path)
    mcp = create_server(policy=CapabilityPolicy(frozenset(["personal"]), write_enabled=True), brain=b)

    secret_key = "sk-live-fakeFAKE1234567890fake"
    try:
        result = call(mcp, "brain_record_outcome", {
            "summary": "x", "workspace": "personal",
            "verification": {secret_key: 123},
            "source_assertion": "explicit_user_confirmation",
        })
    except Exception as exc:
        raise AssertionError(f"pre-body FastMCP validation leaked instead of a controlled tool-body error: {exc!r}") from None

    assert result["error"]["code"] == "BRAIN_INVALID_REQUEST"
    assert result["error"]["details"] == {}
    payload = json.dumps(result)
    assert secret_key not in payload and "sk-live" not in payload
    journal_bytes = journal.read_bytes()
    assert secret_key.encode() not in journal_bytes and b"sk-live" not in journal_bytes
    audit_bytes_ = audit_log.read_bytes() if audit_log.exists() else b""
    assert secret_key.encode() not in audit_bytes_ and b"sk-live" not in audit_bytes_
