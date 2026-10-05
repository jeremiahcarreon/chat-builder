import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.deps import AdminUser, DbSession
from app.evals.versions import current_version
from app.models import ChatApp, Conversation, DataSource
from app.providers import PROVIDERS

router = APIRouter(prefix="/api/apps", tags=["apps"])

LONG_TEXT = 10_000


class AppIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)
    welcome_message: str = Field(default="", max_length=2_000)
    provider: str
    model: str = Field(min_length=1, max_length=200)
    personality: str = Field(default="", max_length=LONG_TEXT)
    knowledge_notes: str = Field(default="", max_length=50_000)
    in_scope: str = Field(default="", max_length=LONG_TEXT)
    out_of_scope: str = Field(default="", max_length=LONG_TEXT)
    refusal_message: str = Field(default="", max_length=2_000)
    strict_mode: bool = True
    # None uses the model's default.
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)
    max_answer_tokens: int = Field(default=4096, ge=64, le=64_000)
    messages_per_minute: int = Field(default=10, ge=1, le=600)
    daily_token_limit: int = Field(default=500_000, ge=1_000, le=1_000_000_000)
    is_public: bool = False
    classifier_enabled: bool = True
    classifier_provider: str | None = None
    classifier_model: str | None = Field(default=None, max_length=200)
    classifier_strictness: str = "balanced"
    classifier_fail_open: bool = True

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        if value not in PROVIDERS:
            raise ValueError(f"Unknown provider: {value}")
        if not PROVIDERS[value].chat:
            raise ValueError(f"{PROVIDERS[value].label} can only be used as the classifier")
        return value

    @field_validator("classifier_provider", "classifier_model")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("classifier_provider")
    @classmethod
    def _known_classifier_provider(cls, value: str | None) -> str | None:
        if value is not None and value not in PROVIDERS:
            raise ValueError(f"Unknown provider: {value}")
        return value

    @field_validator("classifier_strictness")
    @classmethod
    def _strictness(cls, value: str) -> str:
        if value not in ("lenient", "balanced", "strict"):
            raise ValueError("Must be lenient, balanced or strict")
        return value

    @field_validator("name", "model")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Required")
        return value


class AppOut(AppIn):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    source_count: int = 0
    conversation_count: int = 0


def _out(app: ChatApp, source_count: int = 0, conversation_count: int = 0) -> AppOut:
    data = {name: getattr(app, name) for name in AppOut.model_fields if hasattr(app, name)}
    return AppOut(**data, source_count=source_count, conversation_count=conversation_count)


def owned_app(db: DbSession, user: AdminUser, app_id: uuid.UUID) -> ChatApp:
    app = db.get(ChatApp, app_id)
    if app is None or not user.is_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat app not found")
    return app


@router.get("")
def list_apps(db: DbSession, user: AdminUser) -> list[AppOut]:
    sources = (
        select(DataSource.app_id, func.count().label("n")).group_by(DataSource.app_id).subquery()
    )
    conversations = (
        select(Conversation.app_id, func.count().label("n")).group_by(Conversation.app_id).subquery()
    )
    rows = db.execute(
        select(ChatApp, func.coalesce(sources.c.n, 0), func.coalesce(conversations.c.n, 0))
        .outerjoin(sources, sources.c.app_id == ChatApp.id)
        .outerjoin(conversations, conversations.c.app_id == ChatApp.id)
        
        .order_by(ChatApp.created_at.desc())
    ).all()
    return [_out(app, n_sources, n_conversations) for app, n_sources, n_conversations in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_app(body: AppIn, db: DbSession, user: AdminUser) -> AppOut:
    app = ChatApp(owner_id=user.id, **body.model_dump())
    db.add(app)
    db.flush()
    current_version(db, app)
    db.commit()
    return _out(app)


@router.get("/{app_id}")
def get_app(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> AppOut:
    return _out(owned_app(db, user, app_id))


@router.put("/{app_id}")
def update_app(app_id: uuid.UUID, body: AppIn, db: DbSession, user: AdminUser) -> AppOut:
    app = owned_app(db, user, app_id)
    for name, value in body.model_dump().items():
        setattr(app, name, value)
    db.flush()
    current_version(db, app)
    db.commit()
    return _out(app)


@router.delete("/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_app(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> None:
    app = owned_app(db, user, app_id)
    db.delete(app)
    db.commit()
