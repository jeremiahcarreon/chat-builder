import asyncio
import uuid
from types import SimpleNamespace

from app.llm import engine
from app.llm.base import StepResult, ToolCall, ToolResult


class FakeSession:
    """Writes a note and looks something up, then writes the answer."""

    def __init__(self):
        self.round = 0
        self.accepts_user_messages = True

    async def step(self, tools, on_text, on_reset):
        self.round += 1
        if self.round == 1:
            await on_text("Checking the catalog.")
            return StepResult(text="Checking the catalog.", tool_calls=[ToolCall("1", "lookup", {})], stop_reason="tool_use")
        await on_text("It fits.")
        return StepResult(text="It fits.")

    def retry_empty(self):
        return False

    def add_tool_results(self, results):
        pass


def _answer(monkeypatch, final_only):
    ctx = SimpleNamespace(source_names=[], has_documents=False, mcp_tools=[], call_context={}, excerpts=[], table_descriptions=[])

    async def gather(*args):
        return ctx

    async def run_tool(ctx, call):
        return ToolResult(call, "{}")

    monkeypatch.setattr(engine.context_module, "gather", gather)
    monkeypatch.setattr(engine.context_module, "tools_for", lambda ctx: [])
    monkeypatch.setattr(engine.context_module, "run_tool", run_tool)
    monkeypatch.setattr(engine.context_module, "describe_call", lambda ctx, call: call.name)
    monkeypatch.setattr(engine, "build_system_prompt", lambda *a, **k: "")
    monkeypatch.setattr(engine, "build_user_turn", lambda *a, **k: "")
    monkeypatch.setattr(engine, "_usage_event", lambda *a, **k: None)
    client = SimpleNamespace(session=lambda **kwargs: FakeSession())
    app = SimpleNamespace(id=uuid.uuid4(), max_answer_tokens=100, temperature=None, seed=None)
    events = []

    async def emit(event):
        events.append(event)

    gen = engine.Generation(meta={})
    asyncio.run(engine.generate(app, client, "x", "m", [], "q", gen, emit, final_only=final_only))
    return gen.text


def test_chat_keeps_notes_between_lookups(monkeypatch):
    assert _answer(monkeypatch, final_only=False) == "Checking the catalog.\n\nIt fits."


def test_api_answer_is_only_the_text_after_the_last_lookup(monkeypatch):
    assert _answer(monkeypatch, final_only=True) == "It fits."
