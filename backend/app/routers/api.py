"""A chat app's API, for other systems (like the ERP drafting replies to eBay messages).

An admin creates a key for one app in its Deploy tab; the key is shown once and
stored only as a hash. Calls send it as "Authorization: Bearer cbk_...".

    POST /api/v1/apps/{app_id}/answer
        {"message": "...", "thread_id": "ebay-123"}  ->  {"answer": "...", "message_id": 42, ...}
        Messages with the same thread_id continue one conversation, so follow-ups
        have context. The answer goes through the same guardrails, data queries and
        automatic model choice as the chat page.

    POST /api/v1/apps/{app_id}/messages/{message_id}/outcome
        {"outcome": "sent" | "edited" | "discarded", "final_text": "..."}
        What staff did with the draft. It shows in the Quality tab as feedback, and an
        edited reply becomes the reference answer when it's turned into a test.

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

from app.db import SessionLocal
from app.deps import AdminUser, DbSession
from app.llm.engine import ChatAccessError, ChatRequest, run_chat
from app.models import AppApiKey, ChatApp, Conversation, Message
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
    message_id: int | None
    conversation_id: uuid.UUID | None
    thread_id: str | None
    model: str | None
    effort: str | None
    # Data the answer used: "Querying Catalog" and the like.
    lookups: list[str]


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
    )
    db.close()
    text, blocked, error = "", None, None
    out = {"message_id": None, "conversation_id": None, "model": None, "effort": None, "lookups": []}
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


class OutcomeOut(BaseModel):
    message_id: int
    outcome: str


@router.post("/api/v1/apps/{app_id}/messages/{message_id}/outcome")
def record_outcome(app_id: uuid.UUID, message_id: int, body: OutcomeIn, key: ApiKey, db: DbSession) -> OutcomeOut:
    """What staff did with a drafted answer: recorded as feedback in the Quality tab."""
    message = db.get(Message, message_id)
    conversation = db.get(Conversation, message.conversation_id) if message else None
    api_conversation = conversation is not None and (conversation.visitor_id or "").startswith("api:")
    if message is None or message.role != "assistant" or conversation.app_id != app_id or not api_conversation:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
    final_text = (body.final_text or "").strip() if body.outcome == "edited" else ""
    message.meta = {**message.meta, "api_outcome": {"outcome": body.outcome, "final_text": final_text or None}}
    # Sent as drafted counts as helpful; edited or unused as not.
    message.rating = 1 if body.outcome == "sent" else -1
    message.feedback = OUTCOME_LABELS[body.outcome]
    message.rated_at = datetime.now(UTC)
    db.commit()
    return OutcomeOut(message_id=message.id, outcome=body.outcome)
