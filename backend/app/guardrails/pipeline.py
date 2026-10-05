"""Checks run on every incoming message before the main model sees it:
rate limits first (free), then the System 1 classifier."""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select

from app.db import SessionLocal
from app.guardrails.classifier import EXCLUDED_REASON, ClassifierError, classify
from app.guardrails.limits import check_limits
from app.llm.base import LLMError
from app.llm.clients import client_for
from app.llm.prompt import refusal_message
from app.models import ChatApp, Chunk, DataSource, SchemaDoc, UsageEvent
from app.providers import PROVIDERS, ProviderError
from app.rag.embeddings import EmbeddingError, embed_query
from app.routing import CloudSwitch, classifier_for, load_cloud_switch

log = logging.getLogger(__name__)


@dataclass
class Verdict:
    allowed: bool
    reply: str = ""
    meta: dict = field(default_factory=dict)
    usage: list[UsageEvent] = field(default_factory=list)


class ClassifierSetupError(ClassifierError):
    """The app's classifier settings are incomplete."""


def classifier_choice(app: ChatApp) -> tuple[str, str]:
    """The (provider, model) used to screen this app's messages."""
    provider = app.classifier_provider or app.provider
    explicit = (app.classifier_model or "").strip()
    if explicit:
        return provider, explicit
    if PROVIDERS[provider].default_classifier_model:
        return provider, PROVIDERS[provider].default_classifier_model
    # No suggested model (local Ollama): the app's own model, if it's on the same provider.
    if provider == app.provider:
        return provider, app.model
    raise ClassifierSetupError(
        f"Choose a classifier model for {PROVIDERS[provider].label} in the app's Guardrails settings."
    )


MAX_DATA_LINES = 20
MAX_COLUMNS_LISTED = 12


def _cloud_switch(owner_id: uuid.UUID) -> CloudSwitch:
    with SessionLocal() as db:
        return load_cloud_switch(db, owner_id)


def _setup(app: ChatApp, provider: str):
    """The classifier client, plus one line per data source describing what it holds."""
    with SessionLocal() as db:
        sources = db.execute(
            select(DataSource.id, DataSource.kind, DataSource.name).where(
                DataSource.app_id == app.id, DataSource.status == "ready"
            )
        ).all()
        tables: dict = {}
        for source_id, table_name, columns in db.execute(
            select(SchemaDoc.source_id, SchemaDoc.table_name, SchemaDoc.columns).where(SchemaDoc.app_id == app.id)
        ):
            names = [c.get("name", "") for c in columns or []][:MAX_COLUMNS_LISTED]
            tables.setdefault(source_id, []).append(f"{table_name.rsplit('.', 1)[-1]} ({', '.join(names)})")
        # The opening line of each document says what it's about better than its file name.
        openings = {
            source_id: content
            for source_id, content in db.execute(
                select(Chunk.source_id, Chunk.content).where(Chunk.app_id == app.id, Chunk.ordinal == 0)
            )
        }
        lines = []
        for source_id, kind, name in sources:
            if kind == "file":
                first = next((ln.strip() for ln in openings.get(source_id, "").splitlines() if ln.strip()), "")
                lines.append(f"document {name}" + (f": {first[:100]}" if first else ""))
            else:
                lines.append(f"{name}: tables " + "; ".join(tables.get(source_id, [])[:5]))
        return client_for(db, app.owner_id, provider), lines[:MAX_DATA_LINES]


# Cosine distance at or below which a question counts as covered by the app's documents.
# Measured with EmbeddingGemma: questions answered by a document scored 0.37-0.53,
# unrelated questions 0.83 and above.
DOCUMENT_MATCH_DISTANCE = 0.6


async def _matches_documents(app: ChatApp, message: str) -> bool:
    try:
        vector = await embed_query(message)
    except EmbeddingError:
        return False

    def nearest() -> float | None:
        with SessionLocal() as db:
            return db.scalar(
                select(func.min(Chunk.embedding.cosine_distance(vector))).where(Chunk.app_id == app.id)
            )

    distance = await asyncio.to_thread(nearest)
    return distance is not None and distance <= DOCUMENT_MATCH_DISTANCE


async def check_message(
    app: ChatApp,
    history: list[tuple[str, str]],
    message: str,
    user_id: uuid.UUID | None,
    visitor_id: str | None,
    apply_limits: bool = True,
) -> Verdict:
    limit_reply = await asyncio.to_thread(check_limits, app, user_id, visitor_id) if apply_limits else None
    if limit_reply:
        return Verdict(False, limit_reply, {"verdict": "rate_limited", "question": message[:500]})

    if not app.classifier_enabled:
        return Verdict(True)

    try:
        switch = await asyncio.to_thread(_cloud_switch, app.owner_id)
        provider, model = classifier_for(app, classifier_choice(app), switch)
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
    excluded = result.reason.startswith(EXCLUDED_REASON)
    if result.verdict == "off_topic" and not excluded and await _matches_documents(app, message):
        # A question the app's own documents closely match is in scope, whatever the
        # classifier thought (small models only see each document's opening line).
        meta.update(verdict="allowed", reason="Closely matches the app's documents")
        return Verdict(True, meta=meta, usage=usage)
    if result.verdict == "allowed":
        return Verdict(True, meta=meta, usage=usage)
    # Off-topic and abusive messages get the same reply, so probing reveals nothing.
    return Verdict(False, refusal_message(app), meta, usage)
