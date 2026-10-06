"""A scripted stand-in for a chat model, for end-to-end tests.

Enabled only with TEST_MODELS=true. It lets the whole chat path (guardrails,
retrieval, data queries, the widget) be checked with predictable answers and
no API keys:

- A message containing a line "SQL: <query>" makes it call sql_query with that
  query on the first source in <available_tables>, then reply with the result.
- Any other message gets "[<model> <effort>] " followed by the first retrieved excerpt
  (or "no excerpts"), so tests can see which model answered and what retrieval found.
- As a classifier it picks the first topic after greetings, and flags
  manipulation when the message tries to override its instructions.
- As a router it picks the top level for messages containing "complex" and the
  first otherwise, and reports the person unsatisfied when they say "wrong".
"""

import json
import re
import uuid

from app.guardrails.classifier import MANIPULATION_CUES
from app.llm.base import (
    ChatSession,
    Completion,
    LLMClient,
    StepResult,
    ToolCall,
    ToolResult,
)

SQL_LINE = re.compile(r"^SQL:\s*(.+)$", re.MULTILINE)
SOURCE_ID = re.compile(r"source_id: ([0-9a-f-]{36})")
EXCERPT = re.compile(r"<excerpt[^>]*>\n(.*?)\n</excerpt>", re.DOTALL)
QUESTION = re.compile(r"<question>\n(.*)\n</question>", re.DOTALL)
LATEST = re.compile(r"<latest_message>\n(.*)\n</latest_message>", re.DOTALL)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


class ScriptedSession(ChatSession):
    def _init_messages(self, history, user_message):
        self.turn = user_message
        self.results: list[ToolResult] = []
        self.called = False

    @property
    def name(self) -> str:
        effort = self.sampling.effort if self.sampling else None
        return f"[{self.model}{' ' + effort if effort else ''}] "

    async def step(self, tools, on_text, on_reset) -> StepResult:
        question = (QUESTION.search(self.turn) or [None, self.turn])[1]
        sql = SQL_LINE.search(question)
        source = SOURCE_ID.search(self.turn)
        if sql and source and not self.called and any(t.name == "sql_query" for t in tools):
            self.called = True
            call = ToolCall(f"call_{uuid.uuid4().hex[:8]}", "sql_query", {"source_id": source[1], "sql": sql[1].strip()})
            return StepResult("", [call], "tool_use", _tokens(self.turn), 20)
        if self.results:
            result = self.results[-1]
            text = self.name + ("Query error: " if result.is_error else "Query result: ") + result.content.strip()
        else:
            excerpt = EXCERPT.search(self.turn)
            text = self.name + (excerpt[1].strip() if excerpt else "no excerpts")
        await on_text(text)
        return StepResult(text, [], "end", _tokens(self.turn), _tokens(text))

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.results.extend(results)


class ScriptedClient(LLMClient):
    def session(self, model, system, history, user_message, max_tokens, sampling=None) -> ChatSession:
        return ScriptedSession(model, system, history, user_message, max_tokens, sampling)

    async def complete(self, model, system, prompt, max_tokens, sampling=None) -> Completion:
        message = (LATEST.search(prompt) or [None, prompt])[1]
        if '"level"' in system:
            block = system.split("Levels:\n", 1)[1].split("\n\n", 1)[0]
            levels = len(block.splitlines())
            level = levels if "complex" in message.lower() else 1
            reply = {"level": level, "unsatisfied": "wrong" in message.lower()}
            return Completion(json.dumps(reply), _tokens(system + prompt), 10)
        manipulation = bool(MANIPULATION_CUES.search(message))
        reply = {"topic": 0 if manipulation else 2, "manipulation": manipulation, "confidence": 0.9}
        return Completion(json.dumps(reply), _tokens(system + prompt), 10)
