"""MCP servers as a data source: tool names, results, and which tools are on."""

import uuid
from types import SimpleNamespace

import pytest

from app.connectors import mcp
from app.models import DataSource


def test_tool_names_fit_provider_rules():
    tool = mcp.McpTool("id", "Champion ERP (prod)", "orders.find by #", "Find", "", {})
    assert tool.model_name == "champion_erp_prod__orders_find_by_"
    long = mcp.McpTool("id", "x" * 80, "y" * 80, "t", "", {})
    assert len(long.model_name) <= 64


def test_result_text_prefers_text_and_truncates():
    text = SimpleNamespace(type="text", text="Order SO-1001: shipped")
    assert mcp.result_text(SimpleNamespace(content=[text], structured_content=None)) == "Order SO-1001: shipped"
    structured = SimpleNamespace(content=[], structured_content={"status": "Shipped"})
    assert mcp.result_text(structured) == '{"status": "Shipped"}'
    huge = SimpleNamespace(content=[SimpleNamespace(type="text", text="x" * 30_000)], structured_content=None)
    assert mcp.result_text(huge).endswith("... (truncated)")


TOOLS = [
    {"name": "find_order", "title": "Find order", "description": "", "input_schema": {}, "read_only": True},
    {"name": "cancel_order", "title": "Cancel", "description": "", "input_schema": {}, "read_only": False},
]


async def _index(config: dict, tools=TOOLS, monkeypatch=None) -> dict:
    async def listed(url, token):
        return tools

    monkeypatch.setattr(mcp, "list_tools", listed)
    source = DataSource(id=uuid.uuid4(), app_id=uuid.uuid4(), kind="mcp", name="ERP", config=config)
    return await mcp.index_mcp(source)


async def test_first_read_turns_on_read_only_tools(monkeypatch):
    stats = await _index({"url": "http://erp/mcp"}, monkeypatch=monkeypatch)
    assert stats["enabled"] == ["find_order"]


async def test_reread_keeps_choices_and_adds_new_read_only_tools(monkeypatch):
    tools = [*TOOLS, {"name": "get_tracking", "title": "Tracking", "description": "", "input_schema": {}, "read_only": True}]
    # The admin turned find_order off and cancel_order on.
    stats = await _index({"url": "u", "tools": TOOLS, "enabled": ["cancel_order"]}, tools, monkeypatch)
    assert stats["enabled"] == ["cancel_order", "get_tracking"]


async def test_tools_newly_marked_read_only_turn_on(monkeypatch):
    before = [{**TOOLS[0], "read_only": False}, TOOLS[1]]
    # find_order wasn't marked read-only before (so it was off); now the server marks it.
    stats = await _index({"url": "u", "tools": before, "enabled": []}, TOOLS, monkeypatch)
    assert stats["enabled"] == ["find_order"]
    # Once it's been read-only, turning it off sticks.
    stats = await _index({"url": "u", "tools": TOOLS, "enabled": []}, TOOLS, monkeypatch)
    assert stats["enabled"] == []


async def test_import_restores_exported_choices(monkeypatch):
    stats = await _index({"url": "u", "imported_enabled": ["cancel_order", "gone_tool"]}, monkeypatch=monkeypatch)
    assert stats["enabled"] == ["cancel_order"]
    assert stats["imported_enabled"] is None


async def test_no_tools_is_an_error(monkeypatch):
    from app.ingest import IngestError

    with pytest.raises(IngestError):
        await _index({"url": "u"}, [], monkeypatch)


def test_call_meta_carries_context_and_the_signed_in_person():
    meta = mcp.call_meta({"ebay_buyer": "b1"}, {"email": "rep@ccs.test", "name": "Rep", "id": "ignored"})
    assert meta == {
        "chat-builder/context": {"ebay_buyer": "b1"},
        "chat-builder/user": {"email": "rep@ccs.test", "name": "Rep"},
    }
    assert mcp.call_meta(None, None) is None
    # A person without an email is nobody the server could match.
    assert mcp.call_meta(None, {"email": "", "name": "Anon"}) is None


async def test_the_person_reaches_the_server_only_when_the_admin_turned_it_on(monkeypatch):
    from app.llm import context
    from app.llm.base import ToolCall

    sent = []

    async def fake_call_tool(url, token, name, arguments, ctx_context, person=None):
        sent.append(person)
        return "ok", False

    tool = mcp.McpTool("src", "ERP", "get_pricing", "Pricing", "", {})
    ctx = context.TurnContext(app=SimpleNamespace(), mcp_tools={tool.model_name: tool},
                              person={"email": "picker@ccs.test", "name": "Picker"})
    monkeypatch.setattr(mcp, "call_tool", fake_call_tool)
    call = ToolCall(id="1", name=tool.model_name, arguments={"skus": ["CC161"]})

    for send_person, expected in ((True, {"email": "picker@ccs.test", "name": "Picker"}), (False, None)):
        monkeypatch.setattr(context, "_mcp_connection", lambda sid, sp=send_person: ("http://erp/mcp", "t", sp))
        await context._run_mcp(ctx, call)
        assert sent[-1] == expected
