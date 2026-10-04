"""Checks run on every incoming message before the main model sees it:
rate limits first (free), then the System 1 classifier."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select

from app.db import SessionLocal
from app.guardrails.classifier import ClassifierError, classify
from app.guardrails.limits import check_limits
from app.llm.base import LLMError
from app.llm.clients import client_for
from app.llm.prompt import refusal_message
from app.models import ChatApp, DataSource, UsageEvent
from app.providers import PROVIDERS, ProviderError

log = logging.getLogger(__name__)


@dataclass
class Verdict:
    allowed: bool
    reply: str = ""
    meta: dict = field(default_factory=dict)
    usage: list[UsageEvent] = field(default_factory=list)


def classifier_choice(app: ChatApp) -> tuple[str, str]:
    """The (provider, model) used to screen this app's messages."""
    provider = app.classifier_provider or app.provider
    model = (app.classifier_model or "").strip() or PROVIDERS[provider].default_classifier_model or app.model
    return provider, model


def _setup(app: ChatApp, provider: str):
    with SessionLocal() as db:
        names = list(
            db.scalars(
                select(DataSource.name).where(DataSource.app_id == app.id, DataSource.status == "ready")
            )
        )
        return client_for(db, app.owner_id, provider), names


async def check_message(
    app: ChatApp,
    history: list[tuple[str, str]],
    message: str,
    user_id: uuid.UUID | None,
    visitor_id: str | None,
) -> Verdict:
    limit_reply = await asyncio.to_thread(check_limits, app, user_id, visitor_id)
    if limit_reply:
        return Verdict(False, limit_reply, {"verdict": "rate_limited", "question": message[:500]})

    if not app.classifier_enabled:
        return Verdict(True)

    provider, model = classifier_choice(app)
    try:
        client, source_names = await asyncio.to_thread(_setup, app, provider)
        result = await classify(client, model, app, source_names, history, message)
    except (ProviderError, LLMError, ClassifierError) as exc:
        log.warning("Classifier failed for app %s: %s", app.id, exc)
        if app.classifier_fail_open:
            return Verdict(True)
        return Verdict(
            False,
            "Sorry, I can't take questions right now. Please try again later.",
            {"verdict": "classifier_error", "reason": str(exc)[:300], "question": message[:500]},
        )

    usage = []
    if result.completion:
        usage.append(
            UsageEvent(
                app_id=app.id,
                kind="classifier",
                provider=provider,
                model=model,
                input_tokens=result.completion.input_tokens,
                output_tokens=result.completion.output_tokens,
            )
        )
    meta = {
        "verdict": result.verdict,
        "confidence": result.confidence,
        "reason": result.reason,
        "classifier": f"{provider}/{model}",
        "question": message[:500],
    }
    if result.verdict == "allowed":
        return Verdict(True, meta=meta, usage=usage)
    # Off-topic and abusive messages get the same reply, so probing reveals nothing.
    return Verdict(False, refusal_message(app), meta, usage)
