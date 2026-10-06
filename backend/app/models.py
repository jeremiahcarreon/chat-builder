"""Database tables.

The full data model from OVERVIEW.md is created up front so later build steps
only add features, not structural rewrites.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# EmbeddingGemma's native output size.
EMBEDDING_DIM = 768


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="", server_default="", nullable=False)
    # Set for people signed in by an embedding site (the site's own username).
    username: Mapped[str | None] = mapped_column(String(200), unique=True)
    # None until an invited person sets a password; embed-only people never have one.
    password_hash: Mapped[str | None] = mapped_column(Text)
    # Admins manage apps, keys and settings; members only chat with apps they can access.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # local: created in Chat Builder; embed: created by a signed identity from an embedding site.
    source: Mapped[str] = mapped_column(String(16), default="local", server_default="local", nullable=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # SHA-256 of a one-time link for setting a password (invites and resets).
    invite_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class AppAccess(Base):
    """A member's access to a chat app (admins can use every app)."""

    __tablename__ = "app_access"

    app_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_apps.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    created_at: Mapped[datetime] = _created_at()


class UserSession(Base):
    __tablename__ = "sessions"

    # SHA-256 of the cookie token; the raw token is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # web: dashboard login cookie; embed: a widget session (chat only, never the dashboard).
    kind: Mapped[str] = mapped_column(String(16), default="web", server_default="web", nullable=False)
    # For embed sessions: the app the session was opened for.
    app_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chat_apps.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = _created_at()


class ProviderKey(Base):
    __tablename__ = "provider_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChatApp(Base):
    __tablename__ = "chat_apps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    welcome_message: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    personality: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Business rules and conventions the assistant always needs (part numbering,
    # product relationships, what fields mean). Included in every prompt.
    knowledge_notes: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    in_scope: Mapped[str] = mapped_column(Text, default="", nullable=False)
    out_of_scope: Mapped[str] = mapped_column(Text, default="", nullable=False)
    refusal_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    strict_mode: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # System 1 classifier: screens each message before the main model sees it.
    classifier_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Empty means "use the app's own provider with its suggested classifier model".
    classifier_provider: Mapped[str | None] = mapped_column(String(32))
    classifier_model: Mapped[str | None] = mapped_column(String(200))
    # lenient | balanced | strict
    classifier_strictness: Mapped[str] = mapped_column(
        String(16), default="balanced", server_default="balanced", nullable=False
    )
    # If the classifier fails (provider down, bad output): let the message through, or block it.
    classifier_fail_open: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    # Sampling: None uses the model's default. Sent only to models that accept it.
    temperature: Mapped[float | None] = mapped_column(Float)
    seed: Mapped[int | None] = mapped_column(Integer)
    max_answer_tokens: Mapped[int] = mapped_column(Integer, default=1024, nullable=False)
    messages_per_minute: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    daily_token_limit: Mapped[int] = mapped_column(Integer, default=200_000, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Embeddable widget (see app/routers/embed.py).
    embed_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # login: Chat Builder accounts; signed: identity signed by the embedding site; public: anonymous
    embed_auth: Mapped[str] = mapped_column(String(16), default="signed", server_default="signed", nullable=False)
    # Sites allowed to embed the widget, as origins like https://erp.example.com.
    allowed_domains: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)
    # Encrypted secret the embedding site signs identity tokens with.
    encrypted_embed_secret: Mapped[str | None] = mapped_column(Text)
    widget_color: Mapped[str] = mapped_column(String(16), default="#0f172a", server_default="#0f172a", nullable=False)
    # Automatic model choice (see app/llm/router.py): each question goes to the cheapest
    # tier that fits it, and moves up when the person isn't getting what they need.
    auto_route: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    # Ordered cheapest first: [{"provider", "model", "efforts": [...], "use_for": ""}]
    route_tiers: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)
    # jev | llm (the app's classifier model)
    router: Mapped[str] = mapped_column(String(16), default="jev", server_default="jev", nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # file | spreadsheet | mssql | mysql | mariadb | postgres | sqlite
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Non-secret settings (host, port, database, file path, ...).
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Encrypted secrets (passwords). Never returned to the browser.
    encrypted_secret: Mapped[str | None] = mapped_column(Text)
    # pending | processing | ready | failed
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Page number, heading, etc. for citations.
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)


class SchemaDoc(Base):
    """Description of one table (from a database or spreadsheet) used to pick tables to query."""

    __tablename__ = "schema_docs"
    __table_args__ = (
        Index(
            "ix_schema_docs_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    table_name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    columns: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Anonymous visitors of a public app are identified by a random cookie value.
    visitor_id: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # Quality review after the conversation goes quiet (see app/evals/review.py).
    satisfaction: Mapped[float | None] = mapped_column(Float)  # 0-100
    resolved: Mapped[float | None] = mapped_column(Float)  # probability 0-1
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Message count when reviewed; a conversation that continues is reviewed again.
    reviewed_messages: Mapped[int | None] = mapped_column(Integer)
    # Automatic model choice: the lowest step this conversation may use, raised when
    # the person wasn't satisfied with an answer.
    route_floor: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # user | assistant | tool
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Classifier verdict, retrieved sources, tool calls, etc.
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # Assistant messages: the prompt version that produced the answer.
    prompt_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("prompt_versions.id", ondelete="SET NULL"), index=True
    )
    # Feedback from the person chatting: 1 helpful, -1 not helpful.
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    feedback: Mapped[str | None] = mapped_column(Text)
    rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class PromptVersion(Base):
    """A numbered snapshot of an app's prompt-affecting settings (see app/evals/versions.py)."""

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("app_id", "number"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    template_version: Mapped[str] = mapped_column(String(32), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Optional label from the owner, e.g. "tightened scope".
    note: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    created_at: Mapped[datetime] = _created_at()


class TestCase(Base):
    """A question with the expected outcome, re-run after prompt changes to catch regressions."""

    __tablename__ = "test_cases"

    id: Mapped[uuid.UUID] = _uuid_pk()
    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # answer | refuse
    expectation: Mapped[str] = mapped_column(String(16), nullable=False)
    # Phrases the answer must contain (one per line); case-insensitive.
    must_include: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    # A known-good answer; a judge model checks new answers agree with it.
    reference_answer: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", server_default="", nullable=False)
    # The rated chat message this case was created from, if any.
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created_at()


class TestRun(Base):
    __tablename__ = "test_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_apps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt_version_id: Mapped[int | None] = mapped_column(ForeignKey("prompt_versions.id", ondelete="SET NULL"))
    # queued | running | done | failed
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    passed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TestResult(Base):
    __tablename__ = "test_results"
    __table_args__ = (UniqueConstraint("run_id", "case_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Why it failed, one entry per failed check.
    failures: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _created_at()


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_app_time", "app_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    app_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_apps.id", ondelete="CASCADE"), nullable=False
    )
    # classifier | chat | embedding
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class ServiceHeartbeat(Base):
    """Background services check in here so the status panel can tell they're alive."""

    __tablename__ = "service_heartbeats"

    service: Mapped[str] = mapped_column(String(64), primary_key=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserSetting(Base):
    """Per-user settings stored as JSON, such as the local-to-cloud model switch."""

    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
