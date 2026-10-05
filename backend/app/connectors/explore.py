"""Tools that let the model explore a database's tables beyond the few it's shown
up front: find tables by topic, and read one table's full description."""

import asyncio
import re
import uuid

from sqlalchemy import select

from app.db import SessionLocal
from app.llm.base import ToolCall, ToolResult, ToolSpec
from app.models import SchemaDoc
from app.rag.embeddings import EmbeddingError, embed_query

FIND_LIMIT = 12

FIND_TABLES_TOOL = ToolSpec(
    name="find_tables",
    description=(
        "Search a data source's tables by topic when the tables in <available_tables> aren't enough, "
        "for example 'purchase orders', 'shipments tracking' or 'dealer pricing tiers'. Returns table "
        "names with a short summary. Use describe_table to see a table's columns before querying it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "description": "The source_id from <available_tables>."},
            "search": {"type": "string", "description": "What the table should contain."},
        },
        "required": ["source_id", "search"],
        "additionalProperties": False,
    },
)

DESCRIBE_TABLE_TOOL = ToolSpec(
    name="describe_table",
    description="Get one table's full description: columns, keys, what it joins to, size and the owner's notes.",
    parameters={
        "type": "object",
        "properties": {
            "source_id": {"type": "string", "description": "The source_id from <available_tables>."},
            "table": {"type": "string", "description": "The table name, as returned by find_tables."},
        },
        "required": ["source_id", "table"],
        "additionalProperties": False,
    },
)

EXPLORE_TOOLS = [FIND_TABLES_TOOL, DESCRIBE_TABLE_TOOL]


def _summary(description: str) -> str:
    first = description.splitlines()[0]
    about = next((line for line in description.splitlines() if line.startswith("About this table:")), "")
    return f"{first} {about}".strip()


async def find_tables(source_id: uuid.UUID, search: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", search.lower()) if len(w) > 2]
    try:
        vector = await embed_query(search)
    except EmbeddingError:
        vector = None

    def lookup() -> list[SchemaDoc]:
        with SessionLocal() as db:
            docs = list(db.scalars(select(SchemaDoc).where(SchemaDoc.source_id == source_id)))
            if vector is not None:
                ranked = db.scalars(
                    select(SchemaDoc)
                    .where(SchemaDoc.source_id == source_id)
                    .order_by(SchemaDoc.embedding.cosine_distance(vector))
                    .limit(FIND_LIMIT)
                ).all()
            else:
                ranked = []
            # Name and keyword matches first, then the closest by meaning.
            keyword = [d for d in docs if any(w in d.description.lower() for w in words)]
            keyword.sort(key=lambda d: -sum(w in d.description.lower() for w in words))
            seen, result = set(), []
            for doc in keyword[:FIND_LIMIT // 2] + list(ranked):
                if doc.table_name not in seen:
                    seen.add(doc.table_name)
                    result.append(doc)
            return result[:FIND_LIMIT]

    docs = await asyncio.to_thread(lookup)
    if not docs:
        return "No tables matched."
    return "\n".join(f"- {_summary(d.description)}" for d in docs)


async def describe_table(source_id: uuid.UUID, table: str) -> str:
    def lookup() -> SchemaDoc | None:
        with SessionLocal() as db:
            docs = list(db.scalars(select(SchemaDoc).where(SchemaDoc.source_id == source_id)))
            wanted = table.strip().strip('"[]`').lower()
            exact = next((d for d in docs if d.table_name.lower() == wanted), None)
            return exact or next((d for d in docs if d.table_name.lower().rsplit(".", 1)[-1] == wanted.rsplit(".", 1)[-1]), None)

    doc = await asyncio.to_thread(lookup)
    return doc.description if doc else f"No table named {table} (it may be hidden or not exist). Use find_tables."


async def run_explore_tool(ctx, call: ToolCall) -> ToolResult:
    args = call.arguments if isinstance(call.arguments, dict) else {}
    source_id = str(args.get("source_id", "")).strip()
    if source_id not in ctx.queryable:
        return ToolResult(call, "Unknown source_id. Use one from <available_tables>.", is_error=True)
    if call.name == "find_tables":
        search = str(args.get("search", "")).strip()
        if not search:
            return ToolResult(call, "Provide what the table should contain in 'search'.", is_error=True)
        return ToolResult(call, await find_tables(uuid.UUID(source_id), search))
    table = str(args.get("table", "")).strip()
    if not table:
        return ToolResult(call, "Provide the table name in 'table'.", is_error=True)
    return ToolResult(call, await describe_table(uuid.UUID(source_id), table))
