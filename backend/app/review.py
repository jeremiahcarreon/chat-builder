"""Answers held for a person to review before an anonymous visitor sees them.

An app with "Review answers" on holds every answer to an anonymous visitor (its public page or
a public widget). The visitor sees a short notice; the draft waits in the app's Review tab,
where a person sends it as written, edits it, or discards it. Sent and edited answers then
appear in the visitor's conversation; a discarded one never does.

Signed-in people (admins, members, people signed in by an embedding site) get answers straight
away, and so do API callers, which review on their own side and report back through the
outcome endpoint. Both kinds of review count in the same numbers, so the Review tab shows how
often answers go out unchanged before anyone lets them run unattended.

The decision is stored in the message's meta under "review":
{"status": "held" | "sent" | "edited" | "discarded", "by", "at", "original" (edited only)}.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy import func, select

from app.models import ChatApp, Conversation, Message, UsageEvent, User
from app.pricing import cost, load_custom, price_for

HELD = "held"
DECISIONS = ("sent", "edited", "discarded")
HELD_NOTICE = "Thanks for your question. A member of our team is checking the answer and it will appear here shortly."
DISCARDED_NOTICE = "Thanks for your question. A member of our team will follow up with you directly."


def holds_answer(app: ChatApp, req) -> bool:
    """Whether this chat's answer waits for review: anonymous visitors on a reviewing app only."""
    return bool(app.review_answers) and req.user_id is None and not is_api(req)


def is_api(req) -> bool:
    """An API call (its thread ID is optional, so the API's visitor ID is what marks it)."""
    return req.external_id is not None or (req.visitor_id or "").startswith("api:")


def status_of(meta: dict) -> str | None:
    review = meta.get("review")
    return review.get("status") if isinstance(review, dict) else None


def not_shown(meta: dict) -> bool:
    """A held or discarded draft: not part of what the visitor was told."""
    return status_of(meta) in (HELD, "discarded")


def visible_content(message: Message) -> str:
    """What the visitor sees for an answer, given its review state."""
    status = status_of(message.meta)
    if status == HELD:
        return HELD_NOTICE
    if status == "discarded":
        return DISCARDED_NOTICE
    return message.content


class ReviewError(Exception):
    """A decision that can't be applied (already reviewed, empty edit)."""


def decide(message: Message, decision: str, text: str | None, reviewer: User) -> str | None:
    """Applies a reviewer's decision. Returns the edited text when it changed (worth a lesson)."""
    if status_of(message.meta) != HELD:
        raise ReviewError("This answer has already been reviewed.")
    if decision not in DECISIONS:
        raise ReviewError("Choose send, edit or discard.")
    review = {"status": decision, "by": reviewer.email, "at": datetime.now(UTC).isoformat()}
    changed = None
    if decision == "edited":
        final = (text or "").strip()
        if not final:
            raise ReviewError("Write the answer to send, or discard it.")
        if final != message.content.strip():
            review["original"] = message.content
            message.content = final
            changed = final
        else:
            review["status"] = "sent"
    message.meta = {**message.meta, "review": review}
    # Same reading as an API outcome: sent as drafted is helpful, anything else is not.
    message.rating = 1 if review["status"] == "sent" else -1
    message.rated_at = datetime.now(UTC)
    return changed


@dataclass
class ReviewStats:
    days: int
    # Every answer the app gave in the period, and how many the guardrails refused.
    answers: int
    blocked: int
    # Waiting in this app's Review tab right now (not limited to the period).
    held_now: int
    # Answers given to API callers that haven't reported what they did with them.
    awaiting_caller: int
    # Reviewed in the period, here or by an API caller.
    sent: int
    edited: int
    discarded: int
    # Minutes from answer to decision (reviews made here only), median.
    median_review_minutes: float | None
    cost: float | None
    unpriced: list[str]

    @property
    def reviewed(self) -> int:
        return self.sent + self.edited + self.discarded


def stats(db, app: ChatApp, days: int) -> ReviewStats:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(
        select(Message.meta, Message.created_at, Conversation.visitor_id, Message.created_at >= since)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.app_id == app.id, Message.role == "assistant")
        .where((Message.created_at >= since) | (Message.meta["review"]["status"].astext == HELD))
    ).all()
    answers = blocked = held_now = awaiting = 0
    outcomes = {decision: 0 for decision in DECISIONS}
    minutes: list[float] = []
    for meta, created_at, visitor_id, in_period in rows:
        status = status_of(meta)
        if status == HELD:
            held_now += 1
        if not in_period or meta.get("better_requested"):
            continue
        if meta.get("blocked"):
            blocked += 1
            continue
        if meta.get("excluded"):
            continue
        answers += 1
        if status in DECISIONS:
            outcomes[status] += 1
            at = meta["review"].get("at")
            if at:
                minutes.append((datetime.fromisoformat(at) - created_at).total_seconds() / 60)
        elif (visitor_id or "").startswith("api:"):
            outcome = (meta.get("api_outcome") or {}).get("outcome")
            if outcome in DECISIONS:
                outcomes[outcome] += 1
            else:
                awaiting += 1
    total_cost, unpriced = _cost(db, app, since)
    return ReviewStats(
        days=days, answers=answers, blocked=blocked, held_now=held_now, awaiting_caller=awaiting,
        sent=outcomes["sent"], edited=outcomes["edited"], discarded=outcomes["discarded"],
        median_review_minutes=round(median(minutes), 1) if minutes else None,
        cost=total_cost, unpriced=unpriced,
    )


def _cost(db, app: ChatApp, since: datetime) -> tuple[float | None, list[str]]:
    """What the app's model calls cost in the period, from the same prices as the Usage page."""
    custom = load_custom(db, app.owner_id)
    total, unpriced = 0.0, []
    for provider, model, i, o in db.execute(
        select(UsageEvent.provider, UsageEvent.model, func.sum(UsageEvent.input_tokens), func.sum(UsageEvent.output_tokens))
        .where(UsageEvent.app_id == app.id, UsageEvent.created_at >= since)
        .group_by(UsageEvent.provider, UsageEvent.model)
    ):
        amount = cost(i or 0, o or 0, price_for(provider, model, custom))
        if amount is None:
            unpriced.append(f"{provider}/{model}")
        else:
            total += amount
    return round(total, 6), unpriced
