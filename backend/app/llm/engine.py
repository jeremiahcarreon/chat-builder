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
from app.evals.versions import current_version
from app.guardrails.pipeline import check_message
from app.llm import context as context_module
from app.llm.base import LLMClient, LLMError, ToolResult
from app.llm.clients import client_for
from app.llm.prompt import build_system_prompt, build_user_turn
from app.llm.sampling import Sampling
from app.models import ChatApp, Conversation, Message, UsageEvent
from app.providers import ProviderError
from app.routing import chat_model_for, load_cloud_switch

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
    # The provider and model actually answering (differs from the app's own when the
    # cloud switch is moving local-model apps to a cloud provider).
    provider: str = ""
    model: str = ""
    prompt_version_id: int | None = None
    usage: list[UsageEvent] = field(default_factory=list)


@dataclass
class Generation:
    """One answer as it's produced: text so far, metadata, and token usage."""

    meta: dict
    parts: list[str] = field(default_factory=list)
    usage: list[UsageEvent] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(self.parts).strip()


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
        prepared.provider, prepared.model = chat_model_for(app, load_cloud_switch(db, app.owner_id))
        prepared.prompt_version_id = current_version(db, app).id
        db.commit()
        try:
            prepared.client = client_for(db, app.owner_id, prepared.provider)
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
            prompt_version_id=prep.prompt_version_id,
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
    verdict = await check_message(app, prep.history, req.message, req.user_id, req.visitor_id)
    prep.usage.extend(verdict.usage)
    if verdict.meta.get("verdict") == "allowed":
        screened = {"screened": verdict.meta}
    else:
        screened = {}
    if not verdict.allowed:
        message_id = await asyncio.to_thread(
            _finish, prep, verdict.reply, {"blocked": verdict.meta}, True
        )
        yield {"type": "blocked", "message": verdict.reply}
        yield {"type": "done", "message_id": message_id}
        return

    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    gen = Generation(meta={"model": f"{prep.provider}/{prep.model}", **screened})

    async def converse() -> None:
        try:
            await generate(app, prep.client, prep.provider, prep.model, prep.history, req.message, gen, queue.put)
        finally:
            prep.usage.extend(gen.usage)
            await queue.put(None)

    task = asyncio.create_task(converse())
    try:
        while (event := await queue.get()) is not None:
            yield event
    except BaseException:
        # The browser disconnected: stop generating and keep what was produced.
        task.cancel()
        prep.usage.extend(gen.usage)
        meta = gen.meta
        meta["error"] = meta.get("error") or "cancelled"
        answer = gen.text
        await asyncio.to_thread(_finish, prep, answer or "(no answer)", meta, not answer)
        raise

    meta = gen.meta
    answer = gen.text
    if not answer and not meta.get("error"):
        # The model returned nothing at all.
        answer = "Sorry, I couldn't come up with an answer. Please try rephrasing the question."
        meta["error"] = "empty"
        yield {"type": "error", "message": answer}
    message_id = await asyncio.to_thread(_finish, prep, answer or "(no answer)", meta, not answer or meta.get("error") == "empty")
    yield {"type": "done", "message_id": message_id}


async def generate(
    app: ChatApp,
    client: LLMClient,
    provider: str,
    model: str,
    history: list[tuple[str, str]],
    question: str,
    gen: Generation,
    emit,
    usage_kind: str = "chat",
) -> Generation:
    """Answers one question: retrieval, the model call, and any data queries it makes.

    Shared by live chat (which streams events through `emit`) and test runs.
    Errors are recorded in gen.meta["error"] rather than raised.
    """
    ctx = await context_module.gather(app, question, history)
    tools = context_module.tools_for(ctx)
    gen.meta.setdefault("tool_calls", [])
    gen.meta["sources"] = ctx.source_names
    session = client.session(
        model=model,
        system=build_system_prompt(app, ctx.has_documents, bool(tools)),
        history=history,
        user_message=build_user_turn(question, ctx.excerpts, ctx.table_descriptions),
        max_tokens=app.max_answer_tokens,
        sampling=Sampling(app.temperature, app.seed),
    )

    async def on_text(text: str) -> None:
        gen.parts.append(text)
        await emit({"type": "text", "text": text})

    async def on_reset() -> None:
        gen.parts.clear()
        await emit({"type": "reset"})

    def record(result) -> None:
        gen.usage.append(_usage_event(app, usage_kind, provider, model, result.input_tokens, result.output_tokens))

    retried_empty = False
    try:
        for round_number in range(MAX_TOOL_ROUNDS + 1):
            result = await session.step(tools, on_text, on_reset)
            record(result)
            empty = result.stop_reason == "end" and not result.text.strip() and not result.tool_calls
            if empty and not retried_empty and session.retry_empty():
                retried_empty = True
                result = await session.step(tools, on_text, on_reset)
                record(result)
            if result.stop_reason == "refusal":
                await on_text("\n\nI can't help with that request.")
                gen.meta["refused"] = True
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
                await emit({"type": "tool", "label": context_module.describe_call(ctx, call)})
                outcome = await context_module.run_tool(ctx, call)
                gen.meta["tool_calls"].append({"name": call.name, "arguments": call.arguments, "error": outcome.is_error})
                results.append(outcome)
            session.add_tool_results(results)
            if gen.parts and not gen.parts[-1].endswith("\n"):
                await on_text("\n\n")
    except LLMError as exc:
        gen.meta["error"] = str(exc)
        await emit({"type": "error", "message": str(exc)})
    except Exception:
        log.exception("Answer generation failed")
        gen.meta["error"] = "internal"
        await emit({"type": "error", "message": "Something went wrong while answering."})
    return gen
