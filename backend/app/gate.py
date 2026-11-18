"""The review gate: whether an answer can go out without a person looking first.

Holding every answer doesn't scale, and holding none isn't safe. The gate holds only the
answers that need a person, using three checks:

1. Risk: what the tools behind the answer can do. Lookups are safe. Tools whose changes can be
   undone (an MCP server's destructiveHint of false, or the admin's say-so) are recorded. Any
   other tool that changes data holds the answer.
2. Grounding: a second, cheap model pass compares the answer's factual claims (part numbers,
   fitment, stock, prices, dates, policy terms) with what the lookups, documents and notes
   actually said. An unsupported claim gets one rewrite without it; if one remains, it holds.
3. Earned autonomy: the same pass labels the answer's intent ("fitment", "order status"...).
   Each intent needs approval until people have sent enough of its held answers unchanged
   (by default 95% of the last 100, with at least 50 reviewed). Then it goes out on its own,
   except for a random sample still held as spot checks, so the rate keeps being measured and
   an intent that slips goes back to approval. An admin can pin an intent either way.

Settings live in chat_apps.gate (see GateSettings). The decision is stored in the answer's meta
under "gate": {"status": "auto" | "hold", "intent", "tier", "risk", "grounded", "unsupported",
"rewritten", "reasons"}. Only answers the gate held count toward an intent's record: those are
the ones a person actually judged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.db import SessionLocal
from app.llm.base import LLMClient, LLMError
from app.llm.clients import client_for
from app.llm.sampling import CLASSIFIER_SAMPLING
from app.models import ChatApp, Conversation, Message, UsageEvent
from app.providers import JEV_PROVIDER, PROVIDERS, ProviderError
from app.routing import classifier_for, load_cloud_switch

log = logging.getLogger(__name__)

AUTO, HOLD = "auto", "hold"
TIERS = (AUTO, "approval")
GENERAL = "general"
MAX_INTENTS = 30
MAX_EVIDENCE_CHARS = 24_000
MAX_PIECE_CHARS = 6_000
CHECK_TOKENS = 600



class GateSettings(BaseModel):
    enabled: bool = False
    # The kinds of question this app answers. Empty: everything counts as "general".
    intents: list[str] = Field(default_factory=list, max_length=MAX_INTENTS)
    # Intents an admin has fixed at a tier: {"fitment": "approval", "order status": "auto"}.
    pinned: dict[str, str] = Field(default_factory=dict)
    # Share of held answers sent unchanged that lets an intent go out on its own.
    threshold: float = Field(default=0.95, ge=0.5, le=1.0)
    # Reviewed answers needed before that share counts.
    min_samples: int = Field(default=50, ge=5, le=1_000)
    # How many of the most recent reviewed answers the share is taken over.
    window: int = Field(default=100, ge=10, le=2_000)
    # Share of answers from auto intents still held as spot checks.
    audit_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    # Edits up to this share of the draft's words (or short cuts) count as sent unchanged.
    minor_edit: float = Field(default=0.05, ge=0.0, le=0.5)

    @field_validator("intents")
    @classmethod
    def _intents(cls, value: list[str]) -> list[str]:
        names = [" ".join(v.split()).lower()[:60] for v in value]
        return list(dict.fromkeys(n for n in names if n and n != GENERAL))

    @field_validator("pinned")
    @classmethod
    def _pinned(cls, value: dict[str, str]) -> dict[str, str]:
        return {" ".join(k.split()).lower()[:60]: v for k, v in value.items() if v in TIERS}


def settings_for(app: ChatApp) -> GateSettings:
    try:
        return GateSettings(**(getattr(app, "gate", None) or {}))
    except ValueError:
        return GateSettings()


# ------------------------------------------------------------------ grounding


SYSTEM = """You check a reply drafted for a customer against the evidence it was written from: \
lookup results, document excerpts and the business's own notes.

List each factual claim in the reply that the evidence doesn't support. Claims are statements \
the customer would rely on: part numbers, whether a part fits a vehicle, dimensions and specs, \
stock, prices, dates, tracking, and policy terms. A claim is supported when the evidence states \
it or it follows directly from it. Questions, greetings, apologies, offers to help, and saying \
something wasn't found (when the evidence shows the search found nothing) are not claims.

Also list, in the same way, any sentence written to staff or to the assistant itself rather than \
to the customer ("Per guidance: ...", "Try other names.", "Check the order first."): those must \
never reach the customer. Ignore the staff note, if the reply has one: it isn't sent.

Also say which kind of question the reply answers, choosing exactly one of: {intents}.

Reply with JSON only:
{{"intent": "<one of the kinds>", "unsupported": ["<each unsupported claim, quoted briefly>"]}}"""

PROMPT = """EVIDENCE
{evidence}

CUSTOMER'S MESSAGE
{question}

DRAFTED REPLY
{reply}"""

REWRITE = """A check found these statements in your reply that the lookups and documents don't support:
{claims}

Write the reply again without them. Keep everything that is supported. Where a fact is missing, \
say so plainly or ask the customer the one question that would settle it. Keep any staff note \
as it was. Don't call any tools. Write only the reply."""


@dataclass
class Check:
    intent: str
    unsupported: list[str]
    # False when the check itself couldn't run (no model, bad output).
    ran: bool = True
    usage: list[UsageEvent] = field(default_factory=list)


def evidence_text(notes: str, excerpts: list[tuple[str, str]], results: list[tuple[str, str]], context: dict) -> str:
    """Everything the answer could fairly rely on, trimmed to fit a small model."""
    pieces = []
    if notes.strip():
        pieces.append(f"[Business notes]\n{notes.strip()[:MAX_PIECE_CHARS]}")
    if context:
        pieces.append("[About the sender]\n" + "\n".join(f"{k}: {v}" for k, v in context.items()))
    pieces += [f"[Lookup: {name}]\n{text[:MAX_PIECE_CHARS]}" for name, text in results]
    pieces += [f"[Document: {label}]\n{text[:MAX_PIECE_CHARS]}" for label, text in excerpts]
    text = "\n\n".join(pieces) or "(none: no lookups ran and no documents matched)"
    return text[:MAX_EVIDENCE_CHARS]


def checker_for(app: ChatApp) -> tuple[LLMClient, str, str]:
    """The (client, provider, model) that checks answers: the app's classifier model, or, when the
    classifier is Jev (which only classifies), the small model suggested for the app's provider."""
    from app.guardrails.pipeline import effective_classifier

    with SessionLocal() as db:
        provider, model = effective_classifier(db, app)
        if provider == JEV_PROVIDER:
            provider = app.provider
            model = PROVIDERS[provider].default_classifier_model or app.model
            provider, model = classifier_for(app, (provider, model), load_cloud_switch(db, app.owner_id))
        return client_for(db, app.owner_id, provider), provider, model


def parse_check(text: str, intents: list[str]) -> tuple[str, list[str]]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("No JSON in the check")
    data = json.loads(match.group(0))
    intent = " ".join(str(data.get("intent", "")).split()).lower()
    if intent not in intents:
        intent = next((i for i in intents if i in intent or intent in i), GENERAL) if intent else GENERAL
    claims = data.get("unsupported") or []
    if not isinstance(claims, list):
        raise ValueError("unsupported isn't a list")
    return intent, [str(c).strip()[:300] for c in claims if str(c).strip()][:10]


async def check(app: ChatApp, question: str, reply: str, evidence: str) -> Check:
    settings = settings_for(app)
    intents = settings.intents or [GENERAL]
    try:
        client, provider, model = await asyncio.to_thread(checker_for, app)
        completion = await client.complete(
            model=model,
            system=SYSTEM.format(intents=", ".join(f'"{i}"' for i in intents)),
            prompt=PROMPT.format(evidence=evidence, question=question[:4_000], reply=reply[:8_000]),
            max_tokens=CHECK_TOKENS,
            sampling=CLASSIFIER_SAMPLING,
        )
    except (ProviderError, LLMError) as exc:
        log.warning("Answer check failed for app %s: %s", app.id, exc)
        return Check(GENERAL, [], ran=False)
    usage = [UsageEvent(app_id=app.id, kind="gate", provider=provider, model=model,
                        input_tokens=completion.input_tokens, output_tokens=completion.output_tokens)]
    try:
        intent, unsupported = parse_check(completion.text, intents)
    except ValueError as exc:
        log.warning("Answer check for app %s returned unreadable output: %s", app.id, exc)
        return Check(GENERAL, [], ran=False, usage=usage)
    return Check(intent, unsupported, usage=usage)


# ------------------------------------------------------------------ earned autonomy


@dataclass
class IntentRecord:
    intent: str
    tier: str
    pinned: bool
    # Of the most recent held answers people decided on (up to the window).
    reviewed: int
    sent: int

    @property
    def sent_rate(self) -> float | None:
        return round(self.sent / self.reviewed, 3) if self.reviewed else None


def outcome_of(meta: dict, minor_edit: float | None = None, content: str = "") -> str | None:
    """What a person did with an answer: reviewed here, or reported by an API caller. With
    `minor_edit`, an edit that small counts as "sent": the draft was right."""
    outcome = _outcome(meta)
    if outcome == "edited" and minor_edit is not None:
        from app.edits import is_minor
        from app.review import edit_of

        review = meta.get("review") if isinstance(meta.get("review"), dict) else None
        if review and review.get("status") == "edited":
            size = edit_of(review, review.get("original"), content)
        else:
            api = meta.get("api_outcome") or {}
            size = edit_of(api, content, api.get("final_text"))
        if is_minor(size, minor_edit):
            return "sent"
    return outcome


def _outcome(meta: dict) -> str | None:
    review = meta.get("review")
    if isinstance(review, dict) and review.get("status") in ("sent", "edited", "discarded"):
        return review["status"]
    outcome = (meta.get("api_outcome") or {}).get("outcome")
    return outcome if outcome in ("sent", "edited", "discarded") else None


def recent_outcomes(db, app: ChatApp, intent: str, window: int, minor_edit: float | None = None) -> list[str]:
    rows = db.execute(
        select(Message.meta, Message.content)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.app_id == app.id,
            Message.role == "assistant",
            Message.meta["gate"]["status"].astext == HOLD,
            Message.meta["gate"]["intent"].astext == intent,
        )
        .order_by(Message.id.desc())
        .limit(window * 2)
    ).all()
    outcomes = [o for o in (outcome_of(meta, minor_edit, content) for meta, content in rows) if o]
    return outcomes[:window]


def earned_tier(settings: GateSettings, outcomes: list[str]) -> str:
    sent = outcomes.count("sent")
    if len(outcomes) >= settings.min_samples and sent / len(outcomes) >= settings.threshold:
        return AUTO
    return "approval"


def record_for(db, app: ChatApp, intent: str, settings: GateSettings | None = None) -> IntentRecord:
    settings = settings or settings_for(app)
    outcomes = recent_outcomes(db, app, intent, settings.window, settings.minor_edit)
    pinned = settings.pinned.get(intent)
    tier = pinned or earned_tier(settings, outcomes)
    return IntentRecord(intent, tier, pinned is not None, len(outcomes), outcomes.count("sent"))


def records(db, app: ChatApp) -> list[IntentRecord]:
    settings = settings_for(app)
    names = list(dict.fromkeys([*settings.intents, GENERAL, *settings.pinned]))
    return [record_for(db, app, name, settings) for name in names]


# ------------------------------------------------------------------ the decision

RISK_ORDER = {"read": 0, "reversible": 1, "irreversible": 2}


def risk_of(tool_calls: list[dict]) -> tuple[str, list[str]]:
    """The highest risk among the tools that ran, and the irreversible ones by name."""
    ran = [c for c in tool_calls if not c.get("error")]
    level = max((c.get("risk", "read") for c in ran), key=lambda r: RISK_ORDER.get(r, 2), default="read")
    risky = list(dict.fromkeys(c["name"] for c in ran if c.get("risk") == "irreversible"))
    return level, risky


def decide(settings: GateSettings, record: IntentRecord, meta: dict, result: Check, rewritten: bool,
           roll: float | None = None) -> dict:
    """The gate's verdict for one answer, as stored in its meta."""
    risk, risky = risk_of(meta.get("tool_calls", []))
    reasons = []
    if risky:
        reasons.append("Changed data with " + ", ".join(risky))
    if any(c.get("error") for c in meta.get("tool_calls", [])):
        reasons.append("A lookup failed")
    if not result.ran:
        reasons.append("Couldn't check the answer against the lookups")
    elif result.unsupported:
        reasons.append("Not supported by the lookups: " + "; ".join(result.unsupported[:3]))
    if record.tier != AUTO:
        if record.pinned:
            reasons.append(f"{record.intent.capitalize()} answers always need approval")
        else:
            reasons.append(
                f"{record.intent.capitalize()} answers need approval until {round(settings.threshold * 100)}% of "
                f"at least {settings.min_samples} reviewed go out unchanged ({record.sent} of {record.reviewed} so far)"
            )
    elif not reasons and (random.random() if roll is None else roll) < settings.audit_rate:
        reasons.append("Spot check")
    return {
        "status": HOLD if reasons else AUTO,
        "intent": record.intent,
        "tier": record.tier,
        "risk": risk,
        "grounded": (not result.unsupported) if result.ran else None,
        "unsupported": result.unsupported,
        "rewritten": rewritten,
        "reasons": reasons,
    }


def _record(app: ChatApp, intent: str) -> IntentRecord:
    with SessionLocal() as db:
        return record_for(db, app, intent)


async def verdict(app: ChatApp, meta: dict, result: Check, rewritten: bool) -> dict:
    record = await asyncio.to_thread(_record, app, result.intent)
    return decide(settings_for(app), record, meta, result, rewritten)


def applies(app: ChatApp, req, replaces: int | None) -> bool:
    """Whether this answer goes through the gate: API calls, and anonymous visitors on an app that
    reviews their answers. Signed-in staff get answers straight away."""
    if not settings_for(app).enabled or replaces is not None:
        return False
    from app.review import holds_answer, is_api

    return is_api(req) or holds_answer(app, req)
