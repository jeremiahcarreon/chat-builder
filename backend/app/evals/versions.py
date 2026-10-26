"""Prompt versions: a numbered snapshot of everything that shapes an app's answers.

A new version is recorded whenever an app's prompt-affecting settings change
(or the prompt template in code changes). Every answer stores the version that
produced it, so feedback and test results can be compared across versions.
"""

import hashlib
import json

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.llm.prompt import PROMPT_TEMPLATE_VERSION, build_system_prompt
from app.models import ChatApp, PromptVersion

# Settings captured in each version. Limits (rate, daily tokens) and sharing don't
# change what the assistant says, so they're left out.
VERSIONED_FIELDS = (
    "name",
    "description",
    "personality",
    "knowledge_notes",
    "in_scope",
    "out_of_scope",
    "refusal_message",
    "strict_mode",
    "provider",
    "model",
    "temperature",
    "seed",
    "max_answer_tokens",
    "classifier_enabled",
    "classifier_provider",
    "classifier_model",
    "classifier_strictness",
    "classifier_purpose",
)


# Automatic model choice, recorded only while it's on (so apps without it keep their versions).
ROUTING_FIELDS = ("auto_route", "route_tiers", "router")


def snapshot(app: ChatApp) -> dict:
    fields = VERSIONED_FIELDS + (ROUTING_FIELDS if app.auto_route else ())
    return {field: getattr(app, field) for field in fields}


def _hash(settings: dict) -> str:
    payload = json.dumps({"template": PROMPT_TEMPLATE_VERSION, **settings}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def current_version(db: Session, app: ChatApp) -> PromptVersion:
    """The version matching the app's current settings, recording a new one if they changed."""
    settings = snapshot(app)
    digest = _hash(settings)
    latest = db.scalar(
        select(PromptVersion).where(PromptVersion.app_id == app.id).order_by(PromptVersion.number.desc()).limit(1)
    )
    if latest is not None and latest.content_hash == digest:
        return latest
    number = (db.scalar(select(func.max(PromptVersion.number)).where(PromptVersion.app_id == app.id)) or 0) + 1
    version = PromptVersion(
        app_id=app.id,
        number=number,
        settings=json.loads(json.dumps(settings, default=str)),
        template_version=PROMPT_TEMPLATE_VERSION,
        # The prompt as it reads with documents and data tools available, for review.
        system_prompt=build_system_prompt(app, has_documents=True, data_tools=True),
        content_hash=digest,
    )
    try:
        with db.begin_nested():
            db.add(version)
    except IntegrityError:
        # Another request recorded this number first, usually the same new settings.
        existing = db.scalar(
            select(PromptVersion)
            .where(PromptVersion.app_id == app.id, PromptVersion.content_hash == digest)
            .order_by(PromptVersion.number.desc())
            .limit(1)
        )
        if existing is None:
            raise
        return existing
    return version


def changes(previous: dict | None, current: dict) -> list[str]:
    """Names of the settings that differ from the previous version."""
    if previous is None:
        return []
    return [field for field in VERSIONED_FIELDS + ROUTING_FIELDS if previous.get(field) != current.get(field)]
