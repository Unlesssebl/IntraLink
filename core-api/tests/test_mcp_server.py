import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

mcp_path = str(Path(__file__).resolve().parent.parent.parent / "intralink-mcp")
if mcp_path not in sys.path:
    sys.path.insert(0, mcp_path)

from server import IntraLinkMCPServer


@pytest.mark.asyncio
async def test_mcp_initialize_and_ping():
    server = IntraLinkMCPServer()

    # 1. Initialize
    init_msg = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    })
    resp_raw = await server.process_message(init_msg)
    assert resp_raw is not None
    resp = json.loads(resp_raw)
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "intralink-mcp"
    assert "tools" in resp["result"]["capabilities"]

    # 2. Ping
    ping_msg = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"})
    ping_resp_raw = await server.process_message(ping_msg)
    assert ping_resp_raw is not None
    ping_resp = json.loads(ping_resp_raw)
    assert ping_resp["id"] == 2
    assert ping_resp["result"] == {}


@pytest.mark.asyncio
async def test_mcp_tools_list():
    server = IntraLinkMCPServer()
    list_msg = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    resp_raw = await server.process_message(list_msg)
    assert resp_raw is not None
    resp = json.loads(resp_raw)
    assert resp["id"] == 3
    tools = resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "triage_batch" in tool_names
    assert "get_ticket_details" in tool_names
    assert "apply_triage_decision" in tool_names
    assert "diagnose_host" in tool_names
    assert "search_kb" in tool_names
    assert "submit_action_command" in tool_names
    assert "list_skills" in tool_names


@pytest.mark.asyncio
async def test_mcp_tools_call():
    server = IntraLinkMCPServer()

    with patch.object(
        server.api_client,
        "get_triage_batch",
        new_callable=AsyncMock,
        return_value={"total_open": 1, "tasks": [{"id": 101}]},
    ), patch.object(
        server.api_client,
        "diagnose_host",
        new_callable=AsyncMock,
        return_value={"host": "WS-01", "is_online": True},
    ):
        # 1. Вызов triage_batch
        call_msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "triage_batch", "arguments": {"limit": 2}},
        })
        resp_raw = await server.process_message(call_msg)
        assert resp_raw is not None
        resp = json.loads(resp_raw)
        assert resp["id"] == 4
        content_text = resp["result"]["content"][0]["text"]
        data = json.loads(content_text)
        assert data["total_open"] == 1

        # 2. Вызов diagnose_host
        diag_msg = json.dumps({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "diagnose_host", "arguments": {"host": "WS-01"}},
        })
        diag_resp_raw = await server.process_message(diag_msg)
        assert diag_resp_raw is not None
        diag_resp = json.loads(diag_resp_raw)
        assert diag_resp["id"] == 5
        diag_data = json.loads(diag_resp["result"]["content"][0]["text"])
        assert diag_data["is_online"] is True
