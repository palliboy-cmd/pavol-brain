import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))

from brain.api import Brain
from brain.config import BrainConfig
from brain.mcp_server import CapabilityPolicy, create_server
from brain.models import SearchRequest
from test_brain_contract import FixtureRepository, FixtureTransport, REPORT


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
