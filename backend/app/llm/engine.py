"""The chat loop: prepares context, streams the model's answer, runs tools, saves the result.

Events streamed to the browser (as JSON):
  {"type": "conversation", "id": ...}         which conversation this turn belongs to
  {"type": "text", "text": ...}               a piece of the answer
  {"type": "reset"}                           discard the answer streamed so far
  {"type": "tool", "label": ...}              the model is querying data
  {"type": "route", "model": ..., ...}        automatic model choice picked this model
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
from app.guardrails.limits import check_limits
from app.llm.router import Route, baseline_label, better_route, can_improve, choose_route, tier_route
from app.llm.sampling import Sampling
from app.models import ChatApp, Conversation, Message, UsageEvent
from app.providers import ProviderError
from app.routing import LOCAL_PROVIDER, chat_model_for, load_cloud_switch

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
    # Re-answer the question behind this answer one step up the model ladder
    # (the "Get a better answer" button); message is then ignored.
    better_than: int | None = None


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
    effort: str | None = None
    prompt_version_id: int | None = None
    usage: list[UsageEvent] = field(default_factory=list)
    # Automatic model choice: the conversation's floor and the step that gave the last answer.
    route_floor: int = 0
    last_step: int | None = None
    # The question being answered, and the answer this one replaces (if any).
    question: str = ""
    replaces: int | None = None


@dataclass
class Generation:
    """One answer as it's produced: text so far, metadata, and token usage."""

    meta: dict
    parts: list[str] = field(default_factory=list)
    usage: list[UsageEvent] = field(default_factory=list)
    # The app's top model when automatic model choice picked this one (for the savings report).
    baseline: str | None = None

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
        if msg.role not in ("user", "assistant") or msg.meta.get("excluded") or msg.meta.get("better_requested"):
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

        if req.better_than is not None:
            return _prepare_better(db, app, req)

        conversation = None
        if req.conversation_id:
            conversation = _owned_conversation(db, app, req, req.conversation_id)
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

        last_step = last_route_step(db, conversation.id, user_msg.id) if app.auto_route else None
        return _prepared(db, app, conversation, user_msg, last_step)


def _owned_conversation(db, app: ChatApp, req: ChatRequest, conversation_id: uuid.UUID) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    owns = conversation is not None and conversation.app_id == app.id and (
        (req.user_id and conversation.user_id == req.user_id)
        or (req.visitor_id and conversation.visitor_id == req.visitor_id)
    )
    if not owns:
        raise ChatAccessError("Conversation not found")
    return conversation


def _prepare_better(db, app: ChatApp, req: ChatRequest) -> Prepared:
    """Sets aside an answer the person wants improved, to answer its question again."""
    old = db.get(Message, req.better_than)
    if old is None or old.role != "assistant" or old.meta.get("blocked") or old.meta.get("excluded"):
        raise ChatAccessError("Answer not found")
    conversation = _owned_conversation(db, app, req, old.conversation_id)
    user_msg = db.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.role == "user", Message.id < old.id)
        .order_by(Message.id.desc())
        .limit(1)
    )
    if user_msg is None:
        raise ChatAccessError("Answer not found")
    # Left out of the conversation's history from now on (and hidden once replaced).
    old.meta = {**old.meta, "better_requested": {}}
    conversation.updated_at = func.now()
    step = old.meta.get("route", {}).get("step")
    prepared = _prepared(db, app, conversation, user_msg, step if isinstance(step, int) else None)
    prepared.replaces = old.id
    return prepared


def _prepared(db, app: ChatApp, conversation: Conversation, user_msg: Message, last_step: int | None) -> Prepared:
    prepared = Prepared(
        app=app,
        conversation_id=conversation.id,
        user_message_id=user_msg.id,
        history=history_for(db, conversation.id, user_msg.id),
        question=user_msg.content,
    )
    prepared.provider, prepared.model = chat_model_for(app, load_cloud_switch(db, app.owner_id))
    prepared.prompt_version_id = current_version(db, app).id
    if app.auto_route:
        prepared.route_floor = conversation.route_floor
        prepared.last_step = last_step
    db.commit()
    try:
        prepared.client = client_for(db, app.owner_id, prepared.provider)
    except ProviderError as exc:
        prepared.setup_error = str(exc)
    db.expunge(app)
    return prepared


def last_route_step(db, conversation_id: uuid.UUID, before_id: int) -> int | None:
    meta = db.scalar(
        select(Message.meta)
        .where(Message.conversation_id == conversation_id, Message.role == "assistant", Message.id < before_id)
        .order_by(Message.id.desc())
        .limit(1)
    )
    step = (meta or {}).get("route", {}).get("step")
    return step if isinstance(step, int) else None


def routed_model(db, app: ChatApp, route: Route) -> tuple[str, str]:
    """The route's (provider, model), with local steps moved to the cloud when the cloud switch is on."""
    switch = load_cloud_switch(db, app.owner_id)
    if switch.active and route.step.provider == LOCAL_PROVIDER:
        return switch.provider, switch.chat_model
    return route.step.provider, route.step.model


def _unrouted(route: Route, exc: Exception) -> dict:
    """Route details for an answer the app's own model gave instead (no step, so no escalation from it)."""
    return {k: v for k, v in route.meta.items() if k != "step"} | {"error": str(exc)}


def _apply_route(prep: Prepared, route: Route) -> None:
    """Points this turn at the routed model and saves a raised floor on the conversation."""
    with SessionLocal() as db:
        provider, model = routed_model(db, prep.app, route)
        client = client_for(db, prep.app.owner_id, provider)
        if route.floor != prep.route_floor:
            conversation = db.get(Conversation, prep.conversation_id)
            conversation.route_floor = route.floor
            db.commit()
    prep.client, prep.provider, prep.model, prep.effort = client, provider, model, route.step.effort


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
        db.flush()
        if prep.replaces is not None:
            old = db.get(Message, prep.replaces)
            if exclude_turn:
                # No better answer came back: the original stays.
                old.meta = {k: v for k, v in old.meta.items() if k != "better_requested"}
            else:
                # Counts as "not helpful" feedback; the reason, if given, arrives separately.
                old.meta = {**old.meta, "better_requested": {"replaced_by": msg.id, "by": meta.get("model")}}
                old.rating = -1
                old.rated_at = func.now()
        elif exclude_turn:
            user_msg = db.get(Message, prep.user_message_id)
            user_msg.meta = {**user_msg.meta, "excluded": True}
        for event in prep.usage:
            db.add(event)
        db.commit()
        return msg.id


def _usage_event(
    app: ChatApp, kind: str, provider: str, model: str, input_tokens: int, output_tokens: int, baseline: str | None = None
):
    return UsageEvent(
        app_id=app.id,
        kind=kind,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        baseline_model=baseline,
    )


def _route_event(prep: Prepared, route: Route) -> dict:
    return {
        "type": "route",
        "model": prep.model,
        "effort": prep.effort,
        "escalated": route.escalated,
        "can_improve": can_improve(prep.app, route.step_index),
    }


async def run_chat(req: ChatRequest) -> AsyncIterator[dict]:
    prep = await asyncio.to_thread(_prepare, req)
    app = prep.app
    yield {"type": "conversation", "id": str(prep.conversation_id)}

    if prep.setup_error:
        await asyncio.to_thread(_finish, prep, prep.setup_error, {"error": True}, True)
        yield {"type": "error", "message": prep.setup_error}
        return

    question = prep.question
    baseline = None
    if prep.replaces is not None:
        # "Get a better answer": the question was already screened; go one step up the ladder.
        refused = await asyncio.to_thread(check_limits, app, req.user_id, req.visitor_id)
        route = better_route(app, prep.route_floor, prep.last_step) if app.auto_route else None
        if route is None and not refused:
            refused = "This answer already came from the most capable model."
        if not refused:
            try:
                await asyncio.to_thread(_apply_route, prep, route)
            except ProviderError as exc:
                refused = str(exc)
        if refused:
            await asyncio.to_thread(_finish, prep, refused, {"error": True}, True)
            yield {"type": "error", "message": refused}
            return
        yield _route_event(prep, route)
        verdict, screened, route_meta, baseline = None, {}, {"route": route.meta, "retry_of": prep.replaces}, baseline_label(app)
    else:
        # Guardrails run before anything is sent to the main model; the model choice runs alongside.
        routing = (
            asyncio.create_task(choose_route(app, prep.history, question, prep.route_floor, prep.last_step))
            if app.auto_route
            else None
        )
        verdict = await check_message(app, prep.history, question, req.user_id, req.visitor_id)
        prep.usage.extend(verdict.usage)
        route = await routing if routing else None
        route_meta = {}
        if route is not None:
            prep.usage.extend(route.usage)
            route_meta = {"route": route.meta}
            if verdict.allowed:
                try:
                    await asyncio.to_thread(_apply_route, prep, route)
                except ProviderError as exc:
                    # The routed provider has no key: answer with the app's own model.
                    log.warning("Routed model unavailable for app %s: %s", app.id, exc)
                    route_meta = {"route": _unrouted(route, exc)}
                else:
                    baseline = baseline_label(app)
                    yield _route_event(prep, route)
        screened = {"screened": verdict.meta} if verdict.meta.get("verdict") == "allowed" else {}
    if verdict is not None and not verdict.allowed:
        message_id = await asyncio.to_thread(
            _finish, prep, verdict.reply, {"blocked": verdict.meta}, True
        )
        yield {"type": "blocked", "message": verdict.reply}
        yield {"type": "done", "message_id": message_id}
        return

    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    gen = Generation(
        meta={"model": f"{prep.provider}/{prep.model}", "effort": prep.effort, **screened, **route_meta},
        baseline=baseline,
    )

    async def converse() -> None:
        try:
            await generate(
                app, prep.client, prep.provider, prep.model, prep.history, question, gen, queue.put, effort=prep.effort
            )
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
    effort: str | None = None,
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
        sampling=Sampling(app.temperature, app.seed, effort),
    )

    async def on_text(text: str) -> None:
        gen.parts.append(text)
        await emit({"type": "text", "text": text})

    async def on_reset() -> None:
        gen.parts.clear()
        await emit({"type": "reset"})

    def record(result) -> None:
        gen.usage.append(
            _usage_event(app, usage_kind, provider, model, result.input_tokens, result.output_tokens, gen.baseline)
        )

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


@dataclass
class OneOffAnswer:
    text: str
    blocked: bool
    meta: dict
    usage: list[UsageEvent]


def _client_and_model(app: ChatApp) -> tuple[LLMClient, str, str]:
    with SessionLocal() as db:
        provider, model = chat_model_for(app, load_cloud_switch(db, app.owner_id))
        return client_for(db, app.owner_id, provider), provider, model


async def answer_once(app: ChatApp, question: str, usage_kind: str = "test", tier: int | None = None) -> OneOffAnswer:
    """Answers a single question exactly as live chat would, without saving a conversation.

    Used by test runs. Rate limits are skipped; tokens are still recorded. With
    `tier`, the answer comes from that tier of the app's model ladder (calibration).
    """
    verdict = await check_message(app, [], question, None, None, apply_limits=False)
    if not verdict.allowed:
        return OneOffAnswer(verdict.reply, True, {"blocked": verdict.meta}, verdict.usage)

    async def ignore(_event: dict) -> None:
        pass

    if tier is not None:
        route = tier_route(app, tier)
        if route is None:
            return OneOffAnswer("This tier no longer exists.", False, {"error": "no tier"}, verdict.usage)
    else:
        route = await choose_route(app, [], question, 0, None) if app.auto_route else None
    effort, route_meta, route_usage = None, {}, list(route.usage) if route else []
    try:
        if route is not None:
            try:
                client, provider, model, effort = await asyncio.to_thread(_routed_client, app, route)
                route_meta = {"route": route.meta}
            except ProviderError as exc:
                if tier is not None:
                    raise
                route_meta = {"route": _unrouted(route, exc)}
                client, provider, model = await asyncio.to_thread(_client_and_model, app)
        else:
            client, provider, model = await asyncio.to_thread(_client_and_model, app)
    except ProviderError as exc:
        return OneOffAnswer(str(exc), False, {"error": str(exc)}, verdict.usage + route_usage)

    gen = Generation(meta={"model": f"{provider}/{model}", "effort": effort, "screened": verdict.meta, **route_meta})
    await generate(app, client, provider, model, [], question, gen, ignore, usage_kind=usage_kind, effort=effort)
    return OneOffAnswer(gen.text, False, gen.meta, verdict.usage + route_usage + gen.usage)


def _routed_client(app: ChatApp, route: Route) -> tuple[LLMClient, str, str, str | None]:
    with SessionLocal() as db:
        provider, model = routed_model(db, app, route)
        return client_for(db, app.owner_id, provider), provider, model, route.step.effort
