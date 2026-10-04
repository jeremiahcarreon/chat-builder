"""The sql_query tool: lets the model read from an app's spreadsheets and databases."""

import asyncio
import logging
import uuid

from sqlalchemy import func, select

from app.connectors.results import MAX_ROWS
from app.connectors.sql_safety import DIALECTS, UnsafeQuery
from app.db import SessionLocal
from app.llm.base import ToolCall, ToolResult, ToolSpec
from app.models import DataSource, SchemaDoc

log = logging.getLogger(__name__)

TABLES_PER_TURN = 8
# Below this many tables, every table is listed regardless of relevance.
LIST_ALL_BELOW = 12
MAX_SQL_CHARS = 10_000

DIALECT_NAMES = {
    "spreadsheet": "PostgreSQL",
    "postgres": "PostgreSQL",
    "mssql": "SQL Server (T-SQL: use TOP instead of LIMIT)",
    "mysql": "MySQL",
    "mariadb": "MariaDB (MySQL syntax)",
    "sqlite": "SQLite",
}

SQL_TOOL = ToolSpec(
    name="sql_query",
    description=(
        "Run one read-only SQL SELECT query against one of this app's data sources (a spreadsheet "
        "or database) and get the results. Only use tables listed in <available_tables>, with the "
        "SQL dialect shown for that source. At most "
        f"{MAX_ROWS} rows are returned, so aggregate (COUNT, SUM, GROUP BY) and filter in SQL "
        "rather than fetching raw rows. If a query fails, read the error and fix the query."
    ),
    parameters={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "The source_id of the data source, from <available_tables>.",
            },
            "sql": {"type": "string", "description": "A single SELECT statement."},
        },
        "required": ["source_id", "sql"],
        "additionalProperties": False,
    },
)


def relevant_tables(app_id: uuid.UUID, query_vector: list[float]) -> list[str]:
    """Descriptions of the tables most relevant to the question, grouped with their source."""
    with SessionLocal() as db:
        ready = (
            select(SchemaDoc, DataSource.kind)
            .join(DataSource, DataSource.id == SchemaDoc.source_id)
            .where(SchemaDoc.app_id == app_id, DataSource.status == "ready")
        )
        total = db.scalar(select(func.count()).select_from(ready.subquery()))
        if total <= LIST_ALL_BELOW:
            rows = db.execute(ready.order_by(SchemaDoc.source_id, SchemaDoc.table_name)).all()
        else:
            rows = db.execute(
                ready.order_by(SchemaDoc.embedding.cosine_distance(query_vector)).limit(TABLES_PER_TURN)
            ).all()
    return [
        f"source_id: {doc.source_id} | dialect: {DIALECT_NAMES[kind]}\n{doc.description}"
        for doc, kind in rows
    ]


def _execute(source_id: uuid.UUID, kind: str, sql: str) -> str:
    if kind == "spreadsheet":
        from app.connectors.spreadsheets import run_sheet_query

        return run_sheet_query(source_id, sql, MAX_ROWS)
    from app.connectors.databases import run_database_query

    return run_database_query(source_id, sql, MAX_ROWS)


async def run_sql_tool(ctx, call: ToolCall) -> ToolResult:
    args = call.arguments
    if not (
        isinstance(args, dict)
        and isinstance(args.get("source_id"), str)
        and isinstance(args.get("sql"), str)
        and args["sql"].strip()
    ):
        return ToolResult(call, "Invalid arguments: provide source_id and sql as strings.", is_error=True)
    source = ctx.queryable.get(args["source_id"].strip())
    if source is None:
        return ToolResult(call, "Unknown source_id. Use a source_id from <available_tables>.", is_error=True)
    if len(args["sql"]) > MAX_SQL_CHARS:
        return ToolResult(call, "The query is too long.", is_error=True)
    kind, _ = source
    if kind not in DIALECTS:
        return ToolResult(call, "This source can't be queried.", is_error=True)
    try:
        output = await asyncio.to_thread(_execute, uuid.UUID(args["source_id"].strip()), kind, args["sql"])
    except (UnsafeQuery, PermissionError) as exc:
        return ToolResult(call, str(exc), is_error=True)
    except Exception as exc:  # noqa: BLE001 - database errors go back to the model to fix the query
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        log.info("Query failed on %s: %s", args["source_id"], message)
        return ToolResult(call, f"Query failed: {message[:500]}", is_error=True)
    return ToolResult(call, output)
