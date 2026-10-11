"""Rate limits: messages per minute per person, and tokens per day per app."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import ChatApp, Conversation, Message, UsageEvent


def messages_last_minute(app_id: uuid.UUID, user_id: uuid.UUID | None, visitor_id: str | None) -> int:
    since = datetime.now(UTC) - timedelta(minutes=1)
    who = Conversation.user_id == user_id if user_id else Conversation.visitor_id == visitor_id
    with SessionLocal() as db:
        return db.scalar(
            select(func.count())
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Conversation.app_id == app_id, who, Message.role == "user", Message.created_at >= since)
        )


# Owner-run work (test runs, calibration, satisfaction reviews) doesn't count toward the
# daily limit, which protects against heavy chat use; a calibration once used up a whole
# day's allowance and locked people out of the app.
NOT_LIMITED = ("test", "review")


def tokens_today(app_id: uuid.UUID) -> int:
    """Chat tokens used since midnight UTC: the main model, the classifier and the router."""
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        return db.scalar(
            select(func.coalesce(func.sum(UsageEvent.input_tokens + UsageEvent.output_tokens), 0)).where(
                UsageEvent.app_id == app_id, UsageEvent.created_at >= midnight, UsageEvent.kind.not_in(NOT_LIMITED)
            )
        )


def check_limits(app: ChatApp, user_id: uuid.UUID | None, visitor_id: str | None) -> str | None:
    """Returns a message for the user if a limit is reached, else None."""
    # The current message is already saved, so it's included in the count.
    if messages_last_minute(app.id, user_id, visitor_id) > app.messages_per_minute:
        return "You're sending messages too quickly. Please wait a minute and try again."
    if tokens_today(app.id) >= app.daily_token_limit:
        return "This assistant has reached its usage limit for today. Please try again tomorrow."
    return None
