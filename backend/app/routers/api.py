"""A chat app's API, for other systems (like the ERP drafting replies to eBay messages).

An admin creates a key for one app in its Deploy tab; the key is shown once and
stored only as a hash. Calls send it as "Authorization: Bearer cbk_...".

    POST /api/v1/apps/{app_id}/answer
        {"message": "...", "thread_id": "ebay-123"}  ->  {"answer": "...", "message_id": 42, ...}
        Messages with the same thread_id continue one conversation, so follow-ups
        have context. The answer goes through the same guardrails, data queries and
        automatic model choice as the chat page.

        With the review gate on, "review" says whether the answer can go out on its own
        ("auto") or needs a person ("hold"), and why (see app/gate.py).

    POST /api/v1/apps/{app_id}/messages/{message_id}/outcome
        {"outcome": "sent" | "edited" | "discarded", "final_text": "...", "reviewed_by": "..."}
        What staff did with the draft. It shows in the Quality tab as feedback, and an
        edited reply becomes the reference answer when it's turned into a test. Outcomes
        of held answers are what let an intent earn "auto".

    GET /api/v1/apps/{app_id}/metrics?days=30
        Volume, holds, review outcomes, cost, and each intent's tier, for a dashboard
        in the calling system.

A key can only reach its own app's API: never the dashboard, other apps, or settings.
"""

import asyncio
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app import gate
from app.db import SessionLocal
from app.deps import AdminUser, DbSession
from app.llm.engine import ChatAccessError, ChatRequest, run_chat
from app.lessons import question_for
from app.models import AppApiKey, ChatApp, Conversation, Message
from app.queue import queue
from app.routers.apps import owned_app

router = APIRouter(tags=["api"])

KEY_PREFIX = "cbk_"
MAX_KEYS_PER_APP = 20
# last_used_at is updated at most this often, to avoid a write on every call.
USAGE_RESOLUTION = timedelta(minutes=1)
OUTCOME_LABELS = {"sent": "Sent as drafted", "edited": "Edited before sending", "discarded": "Not used"}


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# ------------------------------------------------------------ admin: keys


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


class NewApiKey(ApiKeyOut):
    # Shown only in this response.
    key: str


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def _key_out(key: AppApiKey) -> ApiKeyOut:
    return ApiKeyOut(id=key.id, name=key.name, prefix=key.prefix, created_at=key.created_at, last_used_at=key.last_used_at)


@router.get("/api/apps/{app_id}/api-keys")
def list_keys(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> list[ApiKeyOut]:
    app = owned_app(db, user, app_id)
    keys = db.scalars(select(AppApiKey).where(AppApiKey.app_id == app.id).order_by(AppApiKey.created_at))
    return [_key_out(k) for k in keys]


@router.post("/api/apps/{app_id}/api-keys", status_code=status.HTTP_201_CREATED)
def create_key(app_id: uuid.UUID, body: ApiKeyIn, db: DbSession, user: AdminUser) -> NewApiKey:
    app = owned_app(db, user, app_id)
    count = len(db.scalars(select(AppApiKey.id).where(AppApiKey.app_id == app.id)).all())
    if count >= MAX_KEYS_PER_APP:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This app has the maximum number of API keys. Delete one first.")
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    key = AppApiKey(app_id=app.id, name=body.name.strip(), key_hash=_hash(raw), prefix=raw[:12])
    db.add(key)
    db.commit()
    return NewApiKey(**_key_out(key).model_dump(), key=raw)


@router.delete("/api/apps/{app_id}/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(app_id: uuid.UUID, key_id: uuid.UUID, db: DbSession, user: AdminUser) -> None:
    app = owned_app(db, user, app_id)
    key = db.get(AppApiKey, key_id)
    if key is None or key.app_id != app.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    db.delete(key)
    db.commit()


# ------------------------------------------------------------- the API


def api_key(app_id: uuid.UUID, request: Request, db: DbSession) -> AppApiKey:
    """The calling key, which must belong to this app."""
    header = request.headers.get("authorization", "")
    raw = header[7:].strip() if header.lower().startswith("bearer ") else ""
    key = db.scalar(select(AppApiKey).where(AppApiKey.key_hash == _hash(raw))) if raw.startswith(KEY_PREFIX) else None
    # The same answer for a wrong key and another app's key, so keys can't be probed.
    if key is None or key.app_id != app_id or db.get(ChatApp, app_id) is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key for this app.")
    now = datetime.now(UTC)
    if key.last_used_at is None or now - key.last_used_at > USAGE_RESOLUTION:
        key.last_used_at = now
        db.commit()
    return key


ApiKey = Annotated[AppApiKey, Depends(api_key)]


class AnswerIn(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    # Your own ID for the conversation (an eBay thread, a ticket). Same ID, same conversation.
    thread_id: str | None = Field(default=None, min_length=1, max_length=200)
    # Who is asking, as your system knows them, like {"ebay_buyer": "sample_buyer"}. Shown to the
    # model and sent to the app's MCP servers, which can limit lookups to this person.
    context: dict[str, str] | None = Field(default=None, max_length=20)

    @field_validator("context")
    @classmethod
    def _short_context(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        for key, item in value.items():
            if not key.strip() or len(key) > 50 or len(item) > 500:
                raise ValueError("Context keys can be up to 50 characters and values up to 500")
        return {key.strip(): item.strip() for key, item in value.items()}


class AnswerOut(BaseModel):
    answer: str
    # True when the guardrails refused the message (answer is the app's refusal message).
    blocked: bool
    # Why it was blocked: "off_topic", "abuse", "no_reply" (the sender isn't expecting a reply,
    # for apps whose classifier checks that) or "rate_limited". None when not blocked.
    reason: str | None = None
    message_id: int | None
    conversation_id: uuid.UUID | None
    thread_id: str | None
    model: str | None
    effort: str | None
    # Data the answer used: "Querying Catalog" and the like.
    lookups: list[str]
    # The review gate's verdict, when the app has the gate on: {"status": "auto" | "hold",
    # "intent", "tier", "risk", "grounded", "unsupported", "rewritten", "reasons"}.
    review: dict | None = None


@router.post("/api/v1/apps/{app_id}/answer", response_model=AnswerOut)
async def answer(app_id: uuid.UUID, body: AnswerIn, key: ApiKey, db: DbSession):
    chat_request = ChatRequest(
        app_id=app_id,
        message=body.message.strip(),
        conversation_id=None,
        user_id=None,
        # Rate limits count per key.
        visitor_id=f"api:{key.id}",
        external_id=body.thread_id,
        context=body.context,
        final_only=True,
    )
    db.close()
    text, blocked, error = "", None, None
    out = {"message_id": None, "conversation_id": None, "model": None, "effort": None, "lookups": [], "review": None}
    try:
        async for event in run_chat(chat_request):
            match event["type"]:
                case "conversation":
                    out["conversation_id"] = event["id"]
                case "text":
                    text += event["text"]
                case "reset":
                    text = ""
                case "tool":
                    out["lookups"].append(event["label"])
                case "gate":
                    out["review"] = {k: v for k, v in event.items() if k != "type"}
                case "blocked":
                    blocked = event["message"]
                case "error":
                    error = event["message"]
                case "done":
                    out["message_id"] = event["message_id"]
    except ChatAccessError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None

    meta = await asyncio.to_thread(_message_meta, out["message_id"])
    out["model"], out["effort"] = meta.get("model"), meta.get("effort")
    if blocked is not None and meta.get("blocked", {}).get("verdict") == "rate_limited":
        return JSONResponse({"detail": blocked}, status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    if error and not text.strip():
        # The model provider failed (bad key, outage): nothing usable to draft from.
        return JSONResponse({"detail": error, "message_id": out["message_id"]}, status_code=status.HTTP_502_BAD_GATEWAY)
    return AnswerOut(
        answer=(blocked if blocked is not None else text).strip(),
        blocked=blocked is not None,
        reason=meta.get("blocked", {}).get("verdict") if blocked is not None else None,
        thread_id=body.thread_id,
        **out,
    )


def _message_meta(message_id: int | None) -> dict:
    if message_id is None:
        return {}
    with SessionLocal() as db:
        message = db.get(Message, message_id)
        return dict(message.meta) if message else {}


class OutcomeIn(BaseModel):
    outcome: Literal["sent", "edited", "discarded"]
    # What was actually sent, when it was edited.
    final_text: str | None = Field(default=None, max_length=20_000)
    # Who decided, as your system knows them (a username or email), for the dashboard.
    reviewed_by: str | None = Field(default=None, max_length=200)


class OutcomeOut(BaseModel):
    message_id: int
    outcome: str


@router.post("/api/v1/apps/{app_id}/messages/{message_id}/outcome")
async def record_outcome(app_id: uuid.UUID, message_id: int, body: OutcomeIn, key: ApiKey, db: DbSession) -> OutcomeOut:
    """What staff did with a drafted answer: recorded as feedback in the Quality tab."""
    message = db.get(Message, message_id)
    conversation = db.get(Conversation, message.conversation_id) if message else None
    api_conversation = conversation is not None and (conversation.visitor_id or "").startswith("api:")
    if message is None or message.role != "assistant" or conversation.app_id != app_id or not api_conversation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
    final_text = (body.final_text or "").strip() if body.outcome == "edited" else ""
    outcome = {"outcome": body.outcome, "final_text": final_text or None, "at": datetime.now(UTC).isoformat()}
    if body.reviewed_by and body.reviewed_by.strip():
        outcome["by"] = body.reviewed_by.strip()
    message.meta = {**message.meta, "api_outcome": outcome}
    # Sent as drafted counts as helpful; edited or unused as not.
    message.rating = 1 if body.outcome == "sent" else -1
    message.feedback = OUTCOME_LABELS[body.outcome]
    message.rated_at = datetime.now(UTC)
    question = question_for(db, message) if final_text else None
    context = dict(conversation.context or {})
    db.commit()
    if final_text and question and final_text != message.content.strip():
        # What staff changed is worth learning from (see app/lessons.py).
        await _queue_lesson(app_id, question, final_text, message.content, context,
                            {"origin": "outcome", "message_id": message.id})
    return OutcomeOut(message_id=message.id, outcome=body.outcome)


async def _queue_lesson(app_id: uuid.UUID, customer_message: str, reply: str, draft: str | None,
                        context: dict | None, origin: dict) -> None:
    await queue.configure_task("learn_lesson", queue="ingest").defer_async(
        app_id=str(app_id), customer_message=customer_message, reply=reply, draft=draft,
        context=context or None, origin=origin,
    )


class LessonIn(BaseModel):
    # The message a person sent you (the eBay buyer's message, the ticket).
    customer_message: str = Field(min_length=1, max_length=8_000)
    # The reply your staff actually sent.
    reply: str = Field(min_length=1, max_length=8_000)
    # The draft this app suggested, if there was one (so the lesson can note what changed).
    draft: str | None = Field(default=None, max_length=8_000)
    # Same shape as the answer request's context. Used only to understand the exchange; never stored.
    context: dict[str, str] | None = Field(default=None, max_length=20)


@router.post("/api/v1/apps/{app_id}/lessons", status_code=status.HTTP_202_ACCEPTED)
async def teach(app_id: uuid.UUID, body: LessonIn, key: ApiKey, db: DbSession) -> dict:
    """A reply staff sent, for the app to learn from.

    The exchange is turned into a short, anonymised lesson (no names, usernames, order or
    tracking numbers, stores, emails or phone numbers) and kept under the app's
    "Learned from sent replies" data source, where similar future questions retrieve it.
    Edited drafts reported through the outcome endpoint are learned from automatically.
    """
    db.close()
    await _queue_lesson(app_id, body.customer_message.strip(), body.reply.strip(), body.draft, body.context,
                        {"origin": "api"})
    return {"queued": True}


class IntentOut(BaseModel):
    intent: str
    # "auto" (goes out on its own, with spot checks) or "approval".
    tier: str
    # Fixed by an admin rather than earned.
    pinned: bool
    reviewed: int
    sent: int
    sent_rate: float | None


class MetricsOut(BaseModel):
    days: int
    answers: int
    blocked: int
    # Answers the gate let through and answers it held, in the period.
    auto: int
    held: int
    # Held answers still waiting for a decision (here or by the caller).
    waiting: int
    sent: int
    edited: int
    discarded: int
    cost: float | None
    # Why answers were held, most common first: [{"reason", "count"}].
    hold_reasons: list[dict]
    intents: list[IntentOut]


@router.get("/api/v1/apps/{app_id}/metrics")
def metrics(app_id: uuid.UUID, key: ApiKey, db: DbSession, days: int = 30) -> MetricsOut:
    """The review numbers for a dashboard in the calling system (like the ERP)."""
    days = max(1, min(days, 365))
    app = db.get(ChatApp, app_id)
    return MetricsOut(**gate_metrics(db, app, days))


def gate_metrics(db, app: ChatApp, days: int) -> dict:
    from app.review import stats

    base = stats(db, app, days)
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.scalars(
        select(Message.meta)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.app_id == app.id, Message.role == "assistant", Message.created_at >= since,
               Message.meta["gate"].is_not(None))
    ).all()
    auto = held = waiting = 0
    reasons: dict[str, int] = {}
    for meta in rows:
        verdict = meta.get("gate") or {}
        if verdict.get("status") == gate.AUTO:
            auto += 1
            continue
        held += 1
        if gate.outcome_of(meta) is None:
            waiting += 1
        for reason in verdict.get("reasons", []):
            # "Not supported by the lookups: ..." and the like group under their opening words.
            label = reason.split(":")[0].split(" (")[0]
            reasons[label] = reasons.get(label, 0) + 1
    return {
        "days": days, "answers": base.answers, "blocked": base.blocked, "auto": auto, "held": held,
        "waiting": waiting, "sent": base.sent, "edited": base.edited, "discarded": base.discarded,
        "cost": base.cost,
        "hold_reasons": [{"reason": r, "count": n} for r, n in sorted(reasons.items(), key=lambda x: -x[1])],
        "intents": [IntentOut(**r.__dict__, sent_rate=r.sent_rate) for r in gate.records(db, app)],
    }
