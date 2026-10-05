import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, select

from app.deps import AdminUser, DbSession
from app.pricing import cost, load_custom, price_for
from app.pricing import key as price_key
from app.models import ChatApp, Conversation, Message, UsageEvent

router = APIRouter(prefix="/api/usage", tags=["usage"])


class DayUsage(BaseModel):
    date: date
    input_tokens: int
    output_tokens: int
    cost: float = 0.0


class AppUsage(BaseModel):
    app_id: uuid.UUID
    name: str
    input_tokens: int
    output_tokens: int
    questions: int
    blocked: int
    tokens_today: int
    daily_token_limit: int
    # Estimated, in US dollars, from models with a known price.
    cost: float = 0.0


class ModelUsage(BaseModel):
    kind: str
    provider: str
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    # None when no price is set for this model.
    cost: float | None = None


class BlockedMessage(BaseModel):
    app_id: uuid.UUID
    app_name: str
    question: str
    verdict: str
    reason: str
    created_at: datetime


class UsageReport(BaseModel):
    days: int
    input_tokens: int
    output_tokens: int
    questions: int
    blocked: int
    conversations: int
    cost: float
    # Models used in this period with no known price (their cost isn't included).
    unpriced_models: list[str]
    by_day: list[DayUsage]
    by_app: list[AppUsage]
    by_model: list[ModelUsage]
    recent_blocked: list[BlockedMessage]


@router.get("")
def usage_report(db: DbSession, user: AdminUser, days: int = Query(30, ge=1, le=365)) -> UsageReport:
    now = datetime.now(UTC)
    since = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    apps = {a.id: a for a in db.scalars(select(ChatApp))}
    app_ids = list(apps)
    events = select(UsageEvent).where(UsageEvent.app_id.in_(app_ids), UsageEvent.created_at >= since).subquery()

    day = cast(func.timezone("UTC", events.c.created_at), Date)
    per_day = {
        d: (i, o)
        for d, i, o in db.execute(
            select(day, func.sum(events.c.input_tokens), func.sum(events.c.output_tokens)).group_by(day)
        )
    }
    by_day = []
    for offset in range(days):
        d = (since + timedelta(days=offset)).date()
        i, o = per_day.get(d, (0, 0))
        by_day.append(DayUsage(date=d, input_tokens=i or 0, output_tokens=o or 0))

    tokens_by_app = {
        a: (i or 0, o or 0)
        for a, i, o in db.execute(
            select(events.c.app_id, func.sum(events.c.input_tokens), func.sum(events.c.output_tokens)).group_by(
                events.c.app_id
            )
        )
    }
    today_by_app = dict(
        db.execute(
            select(events.c.app_id, func.sum(events.c.input_tokens + events.c.output_tokens))
            .where(events.c.created_at >= midnight)
            .group_by(events.c.app_id)
        ).all()
    )

    messages = (
        select(Conversation.app_id, Message.role, Message.meta, Message.created_at)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.app_id.in_(app_ids), Message.created_at >= since)
        .subquery()
    )
    questions = dict(
        db.execute(select(messages.c.app_id, func.count()).where(messages.c.role == "user").group_by(messages.c.app_id)).all()
    )
    blocked_filter = (messages.c.role == "assistant") & messages.c.meta.has_key("blocked")
    blocked = dict(db.execute(select(messages.c.app_id, func.count()).where(blocked_filter).group_by(messages.c.app_id)).all())
    conversations = db.scalar(
        select(func.count()).select_from(Conversation).where(
            Conversation.app_id.in_(app_ids), Conversation.created_at >= since
        )
    )

    by_app = [
        AppUsage(
            app_id=app.id,
            name=app.name,
            input_tokens=tokens_by_app.get(app.id, (0, 0))[0],
            output_tokens=tokens_by_app.get(app.id, (0, 0))[1],
            questions=questions.get(app.id, 0),
            blocked=blocked.get(app.id, 0),
            tokens_today=today_by_app.get(app.id) or 0,
            daily_token_limit=app.daily_token_limit,
        )
        for app in sorted(apps.values(), key=lambda a: a.name.lower())
    ]

    by_model = [
        ModelUsage(kind=k, provider=p, model=m, requests=n, input_tokens=i or 0, output_tokens=o or 0)
        for k, p, m, n, i, o in db.execute(
            select(
                events.c.kind, events.c.provider, events.c.model, func.count(),
                func.sum(events.c.input_tokens), func.sum(events.c.output_tokens),
            )
            .group_by(events.c.kind, events.c.provider, events.c.model)
            .order_by(func.sum(events.c.input_tokens + events.c.output_tokens).desc())
        )
    ]

    recent = db.execute(
        select(messages.c.app_id, messages.c.meta, messages.c.created_at)
        .where(blocked_filter)
        .order_by(messages.c.created_at.desc())
        .limit(50)
    ).all()
    recent_blocked = [
        BlockedMessage(
            app_id=app_id,
            app_name=apps[app_id].name,
            question=meta["blocked"].get("question", ""),
            verdict=meta["blocked"].get("verdict", ""),
            reason=meta["blocked"].get("reason", ""),
            created_at=created_at,
        )
        for app_id, meta, created_at in recent
    ]

    # Costs: priced per (day, app, provider, model), from built-in and owner-set prices.
    custom = load_custom(db, user.id)
    cost_by_day: dict = {}
    cost_by_app: dict = {}
    unpriced: set[str] = set()
    for d, app_id, provider, model, i, o in db.execute(
        select(day, events.c.app_id, events.c.provider, events.c.model,
               func.sum(events.c.input_tokens), func.sum(events.c.output_tokens))
        .group_by(day, events.c.app_id, events.c.provider, events.c.model)
    ):
        amount = cost(i or 0, o or 0, price_for(provider, model, custom))
        if amount is None:
            unpriced.add(price_key(provider, model))
            continue
        cost_by_day[d] = cost_by_day.get(d, 0.0) + amount
        cost_by_app[app_id] = cost_by_app.get(app_id, 0.0) + amount
    for entry in by_day:
        entry.cost = round(cost_by_day.get(entry.date, 0.0), 6)
    for entry in by_app:
        entry.cost = round(cost_by_app.get(entry.app_id, 0.0), 6)
    for entry in by_model:
        amount = cost(entry.input_tokens, entry.output_tokens, price_for(entry.provider, entry.model, custom))
        entry.cost = round(amount, 6) if amount is not None else None

    return UsageReport(
        days=days,
        cost=round(sum(cost_by_app.values()), 6),
        unpriced_models=sorted(unpriced),
        input_tokens=sum(a.input_tokens for a in by_app),
        output_tokens=sum(a.output_tokens for a in by_app),
        questions=sum(a.questions for a in by_app),
        blocked=sum(a.blocked for a in by_app),
        conversations=conversations or 0,
        by_day=by_day,
        by_app=by_app,
        by_model=by_model,
        recent_blocked=recent_blocked,
    )
