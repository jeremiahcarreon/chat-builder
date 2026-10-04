"""The chat loop: prepares context, streams the model's answer, runs tools, saves the result.

Events streamed to the browser (as JSON):
  {"type": "conversation", "id": ...}         which conversation this turn belongs to
  {"type": "text", "text": ...}               a piece of the answer
  {"type": "reset"}                           discard the answer streamed so far
  {"type": "tool", "label": ...}              the model is querying data
  {"type": "blocked", "message": ...}         the question was refused by the guardrails
  {"type": "error", "message": ...}           something went wrong
  {"type": "done", "message_id": ...}         the turn is complete
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy import func, select

from app.db import SessionLocal
from app.guardrails.pipeline import check_message
from app.llm import context as context_module
from app.llm.base import LLMClient, LLMError, ToolResult
from app.llm.clients import client_for
from app.llm.prompt import build_system_prompt, build_user_turn
from app.models import ChatApp, Conversation, Message, UsageEvent
from app.providers import ProviderError

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
HISTORY_MESSAGES = 20


@dataclass
class ChatRequest:
    app_id: uuid.UUID
    message: str
    conversation_id: uuid.UUID | None
    user_id: uuid.UUID | None
    visitor_id: str | None


@dataclass
class Prepared:
    app: ChatApp
    conversation_id: uuid.UUID
    user_message_id: int
    history: list[tuple[str, str]]
    client: LLMClient | None = None
    setup_error: str | None = None
    usage: list[UsageEvent] = field(default_factory=list)


class ChatAccessError(Exception):
    pass


def history_for(db, conversation_id: uuid.UUID, before_id: int) -> list[tuple[str, str]]:
    """Earlier turns as (role, text), skipping blocked and failed exchanges."""
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.id < before_id)
        .order_by(Message.id.desc())
        .limit(HISTORY_MESSAGES)
    ).all()
    turns: list[tuple[str, str]] = []
    for msg in reversed(rows):
        if msg.role not in ("user", "assistant") or msg.meta.get("excluded"):
            continue
        if turns and turns[-1][0] == msg.role:
            turns[-1] = (msg.role, turns[-1][1] + "\n\n" + msg.content)
        else:
            turns.append((msg.role, msg.content))
    # Conversations must start with a user turn.
    while turns and turns[0][0] != "user":
        turns.pop(0)
    return turns


def _prepare(req: ChatRequest) -> Prepared:
    with SessionLocal() as db:
        app = db.get(ChatApp, req.app_id)
        if app is None:
            raise ChatAccessError("Chat app not found")

        conversation = None
        if req.conversation_id:
            conversation = db.get(Conversation, req.conversation_id)
            owns = conversation is not None and conversation.app_id == app.id and (
                (req.user_id and conversation.user_id == req.user_id)
                or (req.visitor_id and conversation.visitor_id == req.visitor_id)
            )
            if not owns:
                raise ChatAccessError("Conversation not found")
        if conversation is None:
            conversation = Conversation(
                app_id=app.id,
                user_id=req.user_id,
                visitor_id=req.visitor_id,
                title=req.message.strip().splitlines()[0][:80],
            )
            db.add(conversation)
            db.flush()

        user_msg = Message(conversation_id=conversation.id, role="user", content=req.message, meta={})
        db.add(user_msg)
        conversation.updated_at = func.now()
        db.commit()

        prepared = Prepared(
            app=app,
            conversation_id=conversation.id,
            user_message_id=user_msg.id,
            history=history_for(db, conversation.id, user_msg.id),
        )
        try:
            prepared.client = client_for(db, app.owner_id, app.provider)
        except ProviderError as exc:
            prepared.setup_error = str(exc)
        db.expunge(app)
        return prepared


def _finish(prep: Prepared, answer: str, meta: dict, exclude_turn: bool = False) -> int:
    with SessionLocal() as db:
        msg = Message(
            conversation_id=prep.conversation_id,
            role="assistant",
            content=answer,
            meta={**meta, "excluded": exclude_turn} if exclude_turn else meta,
        )
        db.add(msg)
        if exclude_turn:
            user_msg = db.get(Message, prep.user_message_id)
            user_msg.meta = {**user_msg.meta, "excluded": True}
        for event in prep.usage:
            db.add(event)
        db.commit()
        return msg.id


def _usage_event(app: ChatApp, kind: str, provider: str, model: str, input_tokens: int, output_tokens: int):
    return UsageEvent(
        app_id=app.id,
        kind=kind,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


async def run_chat(req: ChatRequest) -> AsyncIterator[dict]:
    prep = await asyncio.to_thread(_prepare, req)
    app = prep.app
    yield {"type": "conversation", "id": str(prep.conversation_id)}

    if prep.setup_error:
        await asyncio.to_thread(_finish, prep, prep.setup_error, {"error": True}, True)
        yield {"type": "error", "message": prep.setup_error}
        return

    # Guardrails run before anything is sent to the main model.
    verdict = await check_message(app, prep.history, req.message)
    prep.usage.extend(verdict.usage)
    if not verdict.allowed:
        message_id = await asyncio.to_thread(
            _finish, prep, verdict.reply, {"blocked": verdict.meta}, True
        )
        yield {"type": "blocked", "message": verdict.reply}
        yield {"type": "done", "message_id": message_id}
        return

    ctx = await context_module.gather(app, req.message, prep.history)
    tools = context_module.tools_for(ctx)
    session = prep.client.session(
        model=app.model,
        system=build_system_prompt(app, ctx.has_documents, bool(tools)),
        history=prep.history,
        user_message=build_user_turn(req.message, ctx.excerpts, ctx.table_descriptions),
        max_tokens=app.max_answer_tokens,
    )

    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    answer_parts: list[str] = []
    meta: dict = {"sources": ctx.source_names, "tool_calls": []}

    async def on_text(text: str) -> None:
        answer_parts.append(text)
        await queue.put({"type": "text", "text": text})

    async def on_reset() -> None:
        answer_parts.clear()
        await queue.put({"type": "reset"})

    async def converse() -> None:
        try:
            for round_number in range(MAX_TOOL_ROUNDS + 1):
                result = await session.step(tools, on_text, on_reset)
                prep.usage.append(
                    _usage_event(app, "chat", app.provider, app.model, result.input_tokens, result.output_tokens)
                )
                if result.stop_reason == "refusal":
                    await on_text("\n\nI can't help with that request.")
                    meta["refused"] = True
                    break
                if result.stop_reason == "max_tokens":
                    await on_text("\n\n*(This answer was cut off at the length limit.)*")
                    break
                if result.stop_reason != "tool_use":
                    break
                if round_number == MAX_TOOL_ROUNDS:
                    await on_text("\n\nI wasn't able to finish looking that up. Please try a more specific question.")
                    break
                results: list[ToolResult] = []
                for call in result.tool_calls:
                    await queue.put({"type": "tool", "label": context_module.describe_call(ctx, call)})
                    outcome = await context_module.run_tool(ctx, call)
                    meta["tool_calls"].append(
                        {"name": call.name, "arguments": call.arguments, "error": outcome.is_error}
                    )
                    results.append(outcome)
                session.add_tool_results(results)
                if answer_parts and not answer_parts[-1].endswith("\n"):
                    await on_text("\n\n")
        except LLMError as exc:
            meta["error"] = str(exc)
            await queue.put({"type": "error", "message": str(exc)})
        except Exception:
            log.exception("Chat turn failed")
            meta["error"] = "internal"
            await queue.put({"type": "error", "message": "Something went wrong while answering."})
        finally:
            await queue.put(None)

    task = asyncio.create_task(converse())
    try:
        while (event := await queue.get()) is not None:
            yield event
    finally:
        if not task.done():
            # The browser disconnected; stop generating.
            task.cancel()
            meta["error"] = meta.get("error") or "cancelled"
        answer = "".join(answer_parts).strip()
        failed = bool(meta.get("error")) and not answer
        message_id = await asyncio.to_thread(
            _finish, prep, answer or "(no answer)", meta, failed
        )
    yield {"type": "done", "message_id": message_id}
