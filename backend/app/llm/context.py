"""Per-question context: document excerpts, relevant tables, and the data query tool."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from app.db import SessionLocal
from app.llm.base import ToolCall, ToolResult, ToolSpec
from app.models import ChatApp, DataSource
from app.rag.embeddings import EmbeddingError, embed_query
from app.rag.retrieval import search_chunks

log = logging.getLogger(__name__)

DOCUMENT_KINDS = {"file"}


@dataclass
class TurnContext:
    app: ChatApp
    has_documents: bool = False
    excerpts: list[tuple[str, str]] = field(default_factory=list)
    table_descriptions: list[str] = field(default_factory=list)
    source_names: list[str] = field(default_factory=list)
    # data source id -> (kind, name) for sources the sql_query tool may use
    queryable: dict[str, tuple[str, str]] = field(default_factory=dict)


def _ready_sources(app_id: uuid.UUID) -> list[tuple[uuid.UUID, str, str]]:
    with SessionLocal() as db:
        return [
            tuple(row)
            for row in db.execute(
                select(DataSource.id, DataSource.kind, DataSource.name).where(
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


async def gather(app: ChatApp, question: str, history: list[tuple[str, str]]) -> TurnContext:
    ctx = TurnContext(app=app)
    sources = await asyncio.to_thread(_ready_sources, app.id)
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
        excerpts = await asyncio.to_thread(search_chunks, app.id, query_vector)
        ctx.excerpts = [(e.label, e.text) for e in excerpts]
        ctx.source_names = list(dict.fromkeys(e.source_name for e in excerpts))
    if ctx.queryable:
        from app.connectors.query import relevant_tables

        ctx.table_descriptions = await asyncio.to_thread(relevant_tables, app.id, query_vector)
    return ctx


def tools_for(ctx: TurnContext) -> list[ToolSpec]:
    if not ctx.queryable:
        return []
    from app.connectors.query import SQL_TOOL

    return [SQL_TOOL]


def describe_call(ctx: TurnContext, call: ToolCall) -> str:
    source = ctx.queryable.get(str((call.arguments or {}).get("source_id")))
    return f"Querying {source[1]}" if source else "Querying data"


async def run_tool(ctx: TurnContext, call: ToolCall) -> ToolResult:
    if call.name == "sql_query":
        from app.connectors.query import run_sql_tool

        return await run_sql_tool(ctx, call)
    return ToolResult(call, f"Unknown tool: {call.name}", is_error=True)
