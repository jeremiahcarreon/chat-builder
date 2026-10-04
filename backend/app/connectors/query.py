"""The sql_query tool (build steps 4-5)."""

import uuid

from app.llm.base import ToolCall, ToolResult, ToolSpec

SQL_TOOL = ToolSpec("sql_query", "", {"type": "object", "properties": {}})


def relevant_tables(app_id: uuid.UUID, query_vector: list[float]) -> list[str]:
    return []


async def run_sql_tool(ctx, call: ToolCall) -> ToolResult:
    return ToolResult(call, "Data queries are not available yet.", is_error=True)
