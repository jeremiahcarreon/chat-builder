"""Satisfaction review: once a conversation goes quiet, Jev scores how satisfied
the person seemed (0-100) and how likely their need was resolved.

Runs every 10 minutes in the worker, and on demand from the Quality tab. Uses
the owner's TypeSafe key; apps whose owner has none are skipped.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select

from app.db import SessionLocal
from app.guardrails.jev import JevClient
from app.llm.base import LLMError
from app.models import ChatApp, Conversation, Message, UsageEvent
from app.providers import JEV_PROVIDER, PROVIDERS, get_api_key, has_key

log = logging.getLogger(__name__)

# A conversation counts as finished after this long without a new message.
QUIET_FOR = timedelta(minutes=30)
BATCH = 50
MESSAGES_REVIEWED = 40
MESSAGE_CHARS = 1500
MISUSE_NOTE = "refused as an attempt to misuse the assistant"


def _due_query(app_id: uuid.UUID | None, quiet_for: timedelta):
    counts = (
        select(Message.conversation_id, func.count().label("n"), func.max(Message.created_at).label("last"))
        .group_by(Message.conversation_id)
        .subquery()
    )
    # Any reply counts, refusals included: a person wrongly refused is exactly
    # the kind of dissatisfaction worth seeing.
    answered = select(Message.conversation_id).where(Message.role == "assistant").distinct().subquery()
    query = (
        select(Conversation.id, counts.c.n)
        .join(counts, counts.c.conversation_id == Conversation.id)
        .join(answered, answered.c.conversation_id == Conversation.id)
        .where(
            counts.c.last <= datetime.now(UTC) - quiet_for,
            or_(Conversation.reviewed_messages.is_(None), Conversation.reviewed_messages < counts.c.n),
        )
        .order_by(counts.c.last)
    )
    if app_id is not None:
        query = query.where(Conversation.app_id == app_id)
    return query


def _due(db, app_id: uuid.UUID | None, quiet_for: timedelta) -> list[tuple[uuid.UUID, int]]:
    """Conversations that have gone quiet and haven't been reviewed at their current length."""
    return list(db.execute(_due_query(app_id, quiet_for).limit(BATCH)).all())


def waiting_count(db, app_id: uuid.UUID) -> int:
    """Conversations not yet reviewed at their current length, whether or not they've gone quiet."""
    return db.scalar(select(func.count()).select_from(_due_query(app_id, timedelta(0)).subquery())) or 0


def _transcript(db, conversation_id: uuid.UUID) -> list[dict]:
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role.in_(("user", "assistant")))
        .order_by(Message.id.desc())
        .limit(MESSAGES_REVIEWED)
    ).all()
    transcript = []
    for message in reversed(rows):
        entry = {"role": "person" if message.role == "user" else "assistant", "text": message.content[:MESSAGE_CHARS]}
        if message.rating is not None:
            entry["rating"] = "helpful" if message.rating == 1 else "not helpful"
            if message.feedback:
                entry["feedback"] = message.feedback[:MESSAGE_CHARS]
        blocked = message.meta.get("blocked")
        if blocked:
            entry["note"] = MISUSE_NOTE if blocked.get("verdict") == "abuse" else "refused as outside the assistant's scope"
        transcript.append(entry)
    return transcript


async def review_conversations(app_id: uuid.UUID | None = None, quiet_for: timedelta = QUIET_FOR) -> int:
    """Reviews due conversations. Returns how many were scored."""
    with SessionLocal() as db:
        due = _due(db, app_id, quiet_for)
    clients: dict[uuid.UUID, JevClient | None] = {}
    reviewed = 0
    for conversation_id, message_count in due:
        with SessionLocal() as db:
            conversation = db.get(Conversation, conversation_id)
            app = db.get(ChatApp, conversation.app_id)
            if app.owner_id not in clients:
                clients[app.owner_id] = (
                    JevClient(get_api_key(db, app.owner_id, JEV_PROVIDER))
                    if has_key(db, app.owner_id, JEV_PROVIDER)
                    else None
                )
            client = clients[app.owner_id]
            transcript = _transcript(db, conversation_id)
            db.expunge(app)
        if client is None or not transcript:
            continue
        if all(entry.get("note") == MISUSE_NOTE for entry in transcript if entry["role"] == "assistant"):
            # Only refused manipulation attempts: not a customer to score. Marked as
            # reviewed (without a score) so it isn't picked up again.
            with SessionLocal() as db:
                conversation = db.get(Conversation, conversation_id)
                conversation.reviewed_at = datetime.now(UTC)
                conversation.reviewed_messages = message_count
                conversation.satisfaction = None
                conversation.resolved = None
                db.commit()
            continue
        model = PROVIDERS[JEV_PROVIDER].default_classifier_model
        try:
            satisfaction, resolved, usage = await client.review_conversation(model, app, transcript)
        except LLMError as exc:
            log.warning("Review of conversation %s failed: %s", conversation_id, exc)
            continue
        with SessionLocal() as db:
            conversation = db.get(Conversation, conversation_id)
            conversation.satisfaction = satisfaction
            conversation.resolved = round(resolved, 4)
            conversation.reviewed_at = datetime.now(UTC)
            conversation.reviewed_messages = message_count
            db.add(UsageEvent(app_id=app.id, kind="review", provider=JEV_PROVIDER, model=model,
                              input_tokens=usage.input_tokens, output_tokens=usage.output_tokens))
            db.commit()
        reviewed += 1
    for client in clients.values():
        if client is not None:
            await client.aclose()
    return reviewed
