"""Per-question context: document excerpts, relevant tables, and the data query tool.

Document retrieval arrives in step 3 and data tools in steps 4-5; until then
this returns empty context.
"""

from dataclasses import dataclass, field

from app.llm.base import ToolCall, ToolResult, ToolSpec
from app.models import ChatApp


@dataclass
class TurnContext:
    app: ChatApp
    has_documents: bool = False
    excerpts: list[tuple[str, str]] = field(default_factory=list)
    table_descriptions: list[str] = field(default_factory=list)
    source_names: list[str] = field(default_factory=list)
    # data source id -> (kind, name) for sources the sql_query tool may use
    queryable: dict[str, tuple[str, str]] = field(default_factory=dict)


async def gather(app: ChatApp, question: str) -> TurnContext:
    return TurnContext(app=app)


def tools_for(ctx: TurnContext) -> list[ToolSpec]:
    return []


def describe_call(ctx: TurnContext, call: ToolCall) -> str:
    return f"Running {call.name}"


async def run_tool(ctx: TurnContext, call: ToolCall) -> ToolResult:
    return ToolResult(call, f"Unknown tool: {call.name}", is_error=True)
