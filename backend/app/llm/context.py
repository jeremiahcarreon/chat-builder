"""Per-question context: document excerpts, relevant tables, the data query tool, and
the tools of connected MCP servers."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from app.db import SessionLocal
from app.llm.base import ToolCall, ToolResult, ToolSpec
from app.models import ChatApp, DataSource
from app.rag.embeddings import EmbeddingError, embed_query
from app.rag.retrieval import TOP_K, search_chunks
from app.rag.skus import find_skus
from app.rag.skus import key as sku_key

log = logging.getLogger(__name__)

DOCUMENT_KINDS = {"file", "lessons"}
TOOL_KINDS = {"mcp"}


@dataclass
class TurnContext:
    app: ChatApp
    has_documents: bool = False
    excerpts: list[tuple[str, str]] = field(default_factory=list)
    table_descriptions: list[str] = field(default_factory=list)
    source_names: list[str] = field(default_factory=list)
    # data source id -> (kind, name) for sources the sql_query tool may use
    queryable: dict[str, tuple[str, str]] = field(default_factory=dict)
    # MCP tools the model may call, by the name the model sees.
    mcp_tools: dict = field(default_factory=dict)
    # What the calling system said about who is asking (the API's "context"), sent along
    # with MCP tool calls so servers can limit what they return.
    call_context: dict = field(default_factory=dict)
    # The signed-in person chatting ({"email", "name"}), sent to MCP servers whose admin
    # turned on "send_person". None for anonymous visitors, API calls and test runs.
    person: dict | None = None


def _ready_sources(app_id: uuid.UUID) -> list[tuple[uuid.UUID, str, str, dict]]:
    with SessionLocal() as db:
        return [
            tuple(row)
            for row in db.execute(
                select(DataSource.id, DataSource.kind, DataSource.name, DataSource.config).where(
                    DataSource.app_id == app_id, DataSource.status == "ready"
                )
            )
        ]


def _search_text(question: str, history: list[tuple[str, str]]) -> str:
    """Short follow-ups ("what about last year?") are searched together with the previous question."""
    previous = next((text for role, text in reversed(history) if role == "user"), None)
    if previous and len(question.split()) < 12:
        return f"{previous}\n{question}"
    return question


async def gather(
    app: ChatApp,
    question: str,
    history: list[tuple[str, str]],
    call_context: dict | None = None,
    person: dict | None = None,
) -> TurnContext:
    from app.connectors.mcp import enabled_tools

    ctx = TurnContext(app=app, call_context=call_context or {}, person=person)
    all_sources = await asyncio.to_thread(_ready_sources, app.id)
    for sid, kind, name, config in all_sources:
        if kind in TOOL_KINDS:
            ctx.mcp_tools.update({t.model_name: t for t in enabled_tools(sid, name, config)})
    sources = [(sid, kind, name) for sid, kind, name, _ in all_sources if kind not in TOOL_KINDS]
    documents = [s for s in sources if s[1] in DOCUMENT_KINDS]
    ctx.has_documents = bool(documents)
    ctx.queryable = {str(sid): (kind, name) for sid, kind, name in sources if kind not in DOCUMENT_KINDS}
    if not sources:
        return ctx

    try:
        query_vector = await embed_query(_search_text(question, history))
    except EmbeddingError as exc:
        log.warning("Retrieval skipped: %s", exc)
        return ctx

    if documents:
        sku_keys = [sku_key(s) for s in find_skus(question)]
        excerpts = await asyncio.to_thread(search_chunks, app.id, query_vector, TOP_K, sku_keys or None)
        ctx.excerpts = [(e.label, e.text) for e in excerpts]
        ctx.source_names = list(dict.fromkeys(e.source_name for e in excerpts))
    if ctx.queryable:
        from app.connectors.query import relevant_tables

        ctx.table_descriptions = await asyncio.to_thread(relevant_tables, app.id, query_vector)
    return ctx


def tools_for(ctx: TurnContext) -> list[ToolSpec]:
    tools = [_mcp_spec(tool) for tool in ctx.mcp_tools.values()]
    if not ctx.queryable:
        return tools
    from app.connectors.explore import EXPLORE_TOOLS
    from app.connectors.query import SQL_TOOL

    # Exploration only helps for connected databases, which can have hundreds of tables.
    databases = any(kind not in ("spreadsheet",) for kind, _ in ctx.queryable.values())
    if databases:
        # Even when no table looks relevant, the model can still search for one.
        return [SQL_TOOL, *EXPLORE_TOOLS, *tools]
    return ([SQL_TOOL] if ctx.table_descriptions else []) + tools


def _mcp_spec(tool) -> ToolSpec:
    schema = dict(tool.input_schema or {})
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    description = f"{tool.title} (from {tool.source_name}). {tool.description}".strip()
    return ToolSpec(name=tool.model_name, description=description[:1024], parameters=schema)


def describe_call(ctx: TurnContext, call: ToolCall) -> str:
    if call.name in ctx.mcp_tools:
        tool = ctx.mcp_tools[call.name]
        return f"{tool.title} ({tool.source_name})"
    source = ctx.queryable.get(str((call.arguments or {}).get("source_id")))
    if call.name in ("find_tables", "describe_table"):
        return f"Looking through {source[1]}'s tables" if source else "Looking through tables"
    return f"Querying {source[1]}" if source else "Querying data"


async def run_tool(ctx: TurnContext, call: ToolCall) -> ToolResult:
    if call.name in ctx.mcp_tools:
        return await _run_mcp(ctx, call)
    if call.name == "sql_query":
        from app.connectors.query import run_sql_tool

        return await run_sql_tool(ctx, call)
    if call.name in ("find_tables", "describe_table"):
        from app.connectors.explore import run_explore_tool

        return await run_explore_tool(ctx, call)
    return ToolResult(call, f"Unknown tool: {call.name}", is_error=True)


def _mcp_connection(source_id: str) -> tuple[str, str | None, bool]:
    from app.connectors.mcp import token_for

    with SessionLocal() as db:
        source = db.get(DataSource, uuid.UUID(source_id))
        return source.config.get("url", ""), token_for(source), bool(source.config.get("send_person"))


async def _run_mcp(ctx: TurnContext, call: ToolCall) -> ToolResult:
    from app.connectors.mcp import McpFailed, call_tool

    tool = ctx.mcp_tools[call.name]
    if call.arguments is None:
        return ToolResult(call, "The arguments weren't valid JSON. Call the tool again with valid arguments.", is_error=True)
    try:
        url, token, send_person = await asyncio.to_thread(_mcp_connection, tool.source_id)
        person = ctx.person if send_person else None
        text, is_error = await call_tool(url, token, tool.name, call.arguments, ctx.call_context, person)
    except McpFailed as exc:
        log.warning("MCP tool %s failed: %s", call.name, exc)
        return ToolResult(call, str(exc), is_error=True)
    return ToolResult(call, text, is_error=is_error)
