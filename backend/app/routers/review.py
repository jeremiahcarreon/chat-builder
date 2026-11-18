"""The Review tab: answers held for a person, and how often they go out unchanged.

    GET  /api/apps/{app_id}/review              held answers, oldest first
    POST /api/apps/{app_id}/review/{message_id} {"decision": "sent" | "edited" | "discarded", "text": "..."}
    GET  /api/apps/{app_id}/review/stats?days=30
    GET  /api/apps/{app_id}/review/gate?days=30  the review gate: holds, reasons and each intent's tier
    PUT  /api/apps/{app_id}/review/intents/{intent}  {"tier": "auto" | "approval" | null} pin or unpin
"""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import gate
from app.deps import AdminUser, DbSession
from app.lessons import question_for
from app.models import Conversation, Message
from app.review import HELD, ReviewError, decide, stats
from app.routers.api import OUTCOME_LABELS, MetricsOut, _queue_lesson, gate_metrics
from app.routers.apps import owned_app

router = APIRouter(prefix="/api/apps/{app_id}/review", tags=["review"])

QUEUE_LIMIT = 200


class ToolCallOut(BaseModel):
    name: str
    arguments: dict | None = None
    error: bool = False


class HeldAnswer(BaseModel):
    message_id: int
    conversation_id: uuid.UUID
    question: str
    draft: str
    tool_calls: list[ToolCallOut]
    sources: list[str]
    model: str | None
    created_at: datetime
    # Why the review gate held it (empty when the app holds every answer).
    reasons: list[str] = []
    intent: str | None = None


class DecisionIn(BaseModel):
    decision: Literal["sent", "edited", "discarded"]
    # The answer to send, when edited.
    text: str | None = Field(default=None, max_length=20_000)


class DecisionOut(BaseModel):
    message_id: int
    status: str


class StatsOut(BaseModel):
    days: int
    answers: int
    blocked: int
    held_now: int
    awaiting_caller: int
    reviewed: int
    sent: int
    edited: int
    discarded: int
    median_review_minutes: float | None
    cost: float | None
    unpriced: list[str]


@router.get("")
def held_answers(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> list[HeldAnswer]:
    owned_app(db, user, app_id)
    rows = db.scalars(
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.app_id == app_id,
            Message.role == "assistant",
            Message.meta["review"]["status"].astext == HELD,
        )
        .order_by(Message.id)
        .limit(QUEUE_LIMIT)
    ).all()
    return [
        HeldAnswer(
            message_id=m.id,
            conversation_id=m.conversation_id,
            question=question_for(db, m) or "",
            draft=m.content,
            tool_calls=[ToolCallOut(**{k: c.get(k) for k in ("name", "arguments")}, error=bool(c.get("error")))
                        for c in m.meta.get("tool_calls", [])],
            sources=list(m.meta.get("sources", [])),
            model=m.meta.get("model"),
            created_at=m.created_at,
            reasons=list((m.meta.get("gate") or {}).get("reasons", [])),
            intent=(m.meta.get("gate") or {}).get("intent"),
        )
        for m in rows
    ]


@router.post("/{message_id}")
async def review_answer(app_id: uuid.UUID, message_id: int, body: DecisionIn, db: DbSession, user: AdminUser) -> DecisionOut:
    owned_app(db, user, app_id)
    message = db.get(Message, message_id)
    conversation = db.get(Conversation, message.conversation_id) if message else None
    if message is None or message.role != "assistant" or conversation is None or conversation.app_id != app_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
    draft = message.content
    try:
        changed = decide(message, body.decision, body.text, user)
    except ReviewError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    final_status = message.meta["review"]["status"]
    message.feedback = OUTCOME_LABELS[final_status]
    question = question_for(db, message) if changed else None
    db.commit()
    if changed and question:
        # What the reviewer changed is worth learning from, the same as an edited API draft.
        await _queue_lesson(app_id, question, changed, draft, None, {"origin": "review", "message_id": message.id})
    return DecisionOut(message_id=message.id, status=final_status)


@router.get("/stats")
def review_stats(app_id: uuid.UUID, db: DbSession, user: AdminUser, days: int = Query(30, ge=1, le=365)) -> StatsOut:
    app = owned_app(db, user, app_id)
    result = stats(db, app, days)
    return StatsOut(**{**result.__dict__, "reviewed": result.reviewed})


@router.get("/gate")
def gate_overview(app_id: uuid.UUID, db: DbSession, user: AdminUser, days: int = Query(30, ge=1, le=365)) -> MetricsOut:
    app = owned_app(db, user, app_id)
    return MetricsOut(**gate_metrics(db, app, days))


class PinIn(BaseModel):
    # None lets the intent earn its tier again.
    tier: Literal["auto", "approval"] | None


@router.put("/intents/{intent}")
def pin_intent(app_id: uuid.UUID, intent: str, body: PinIn, db: DbSession, user: AdminUser) -> dict:
    app = owned_app(db, user, app_id)
    settings = gate.settings_for(app)
    name = " ".join(intent.split()).lower()[:60]
    pinned = {k: v for k, v in settings.pinned.items() if k != name}
    if body.tier is not None:
        pinned[name] = body.tier
    app.gate = settings.model_copy(update={"pinned": pinned}).model_dump()
    db.commit()
    return {"intent": name, "tier": body.tier}
