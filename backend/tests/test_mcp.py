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


async def test_import_restores_exported_choices(monkeypatch):
    stats = await _index({"url": "u", "imported_enabled": ["cancel_order", "gone_tool"]}, monkeypatch=monkeypatch)
    assert stats["enabled"] == ["cancel_order"]
    assert stats["imported_enabled"] is None


async def test_no_tools_is_an_error(monkeypatch):
    from app.ingest import IngestError

    with pytest.raises(IngestError):
        await _index({"url": "u"}, [], monkeypatch)
