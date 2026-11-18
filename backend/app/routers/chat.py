"""Chat endpoints, used by the owner (logged in) and by visitors of public apps."""

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.config import get_settings
from app.deps import DbSession
from app.llm.engine import ChatAccessError, ChatRequest, run_chat
from app.llm.router import can_improve
from app.models import AppAccess, ChatApp, Conversation, Message, User
from app.review import HELD, status_of, visible_content
from app.security import SESSION_COOKIE, user_for_token

router = APIRouter(prefix="/api/chat", tags=["chat"])

VISITOR_COOKIE = "cb_visitor"


@dataclass
class Access:
    app: ChatApp
    # A signed-in person (admin, member with access, or an embedding site's user).
    user: User | None
    # An anonymous visitor of a public app.
    visitor_id: str | None
    new_visitor: bool = False

    @property
    def is_admin(self) -> bool:
        return self.user is not None and self.user.is_admin

    def conversation_filter(self):
        if self.user is not None:
            return Conversation.user_id == self.user.id
        return Conversation.visitor_id == self.visitor_id


def can_use(db, user: User, app: ChatApp) -> bool:
    if user.blocked:
        return False
    if user.is_admin:
        return True
    return db.get(AppAccess, (app.id, user.id)) is not None


def resolve_access(db: DbSession, request: Request, app_id: uuid.UUID) -> Access:
    app = db.get(ChatApp, app_id)
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat app not found")
    # The embedded widget sends its session as a bearer token (third-party iframes
    # can't rely on cookies); the dashboard and chat page use the login cookie.
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        user = user_for_token(db, authorization[7:].strip(), kind="embed", app_id=app.id)
        if user is None or not can_use(db, user, app):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your chat session has ended. Reload the page.")
        return Access(app, user, None)
    token = request.cookies.get(SESSION_COOKIE)
    user = user_for_token(db, token) if token else None
    if user is not None and can_use(db, user, app):
        return Access(app, user, None)
    embed_visitor = request.headers.get("x-visitor-id")
    if embed_visitor and app.embed_enabled and app.embed_auth == "public" and len(embed_visitor) == 43:
        return Access(app, None, embed_visitor)
    if not app.is_public:
        # Same response as a missing app, so private apps can't be discovered.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat app not found")
    visitor = request.cookies.get(VISITOR_COOKIE)
    if visitor and len(visitor) == 43:
        return Access(app, None, visitor)
    return Access(app, None, secrets.token_urlsafe(32), new_visitor=True)


def _remember_visitor(response: Response, access: Access) -> None:
    if access.new_visitor:
        response.set_cookie(
            VISITOR_COOKIE,
            access.visitor_id,
            max_age=365 * 86400,
            httponly=True,
            secure=get_settings().cookie_secure,
            samesite="lax",
        )


class AppInfo(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    welcome_message: str
    is_owner: bool


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    updated_at: datetime


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    blocked: bool = False
    error: bool = False
    # Waiting for a person to review it; content is the notice the visitor sees.
    held: bool = False
    rating: int | None = None
    feedback: str | None = None
    # Automatic model choice can re-answer this one a step up ("Get a better answer").
    can_improve: bool = False
    created_at: datetime


class FeedbackIn(BaseModel):
    rating: Literal[1, -1]
    feedback: str | None = Field(default=None, max_length=4_000)


class SendMessage(BaseModel):
    message: str = Field(min_length=1, max_length=8_000)
    conversation_id: uuid.UUID | None = None


@router.get("/{app_id}")
def app_info(app_id: uuid.UUID, request: Request, response: Response, db: DbSession) -> AppInfo:
    access = resolve_access(db, request, app_id)
    _remember_visitor(response, access)
    app = access.app
    return AppInfo(
        id=app.id,
        name=app.name,
        description=app.description,
        welcome_message=app.welcome_message,
        is_owner=access.is_admin,
    )


@router.get("/{app_id}/conversations")
def list_conversations(
    app_id: uuid.UUID, request: Request, response: Response, db: DbSession
) -> list[ConversationOut]:
    access = resolve_access(db, request, app_id)
    _remember_visitor(response, access)
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.app_id == app_id, access.conversation_filter())
        .order_by(Conversation.updated_at.desc())
        .limit(100)
    )
    return [ConversationOut(id=c.id, title=c.title, updated_at=c.updated_at) for c in rows]


def _owned_conversation(db: DbSession, access: Access, conversation_id: uuid.UUID) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.app_id == access.app.id,
            access.conversation_filter(),
        )
    )
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


@router.get("/{app_id}/conversations/{conversation_id}")
def conversation_messages(
    app_id: uuid.UUID, conversation_id: uuid.UUID, request: Request, db: DbSession
) -> list[MessageOut]:
    access = resolve_access(db, request, app_id)
    _owned_conversation(db, access, conversation_id)
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role.in_(("user", "assistant")))
        .order_by(Message.id)
    )
    return [
        MessageOut(
            id=m.id,
            role=m.role,
            content=visible_content(m) if m.role == "assistant" else m.content,
            blocked=bool(m.meta.get("blocked")),
            error=bool(m.meta.get("error")) and m.meta.get("excluded", False),
            held=status_of(m.meta) == HELD,
            rating=m.rating,
            feedback=m.feedback,
            can_improve=m.role == "assistant" and status_of(m.meta) is None
            and can_improve(access.app, m.meta.get("route", {}).get("step")),
            created_at=m.created_at,
        )
        for m in rows
        # Answers replaced by a better one are hidden.
        if "replaced_by" not in m.meta.get("better_requested", {})
    ]


@router.delete("/{app_id}/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    app_id: uuid.UUID, conversation_id: uuid.UUID, request: Request, db: DbSession
) -> None:
    access = resolve_access(db, request, app_id)
    conversation = _owned_conversation(db, access, conversation_id)
    db.execute(delete(Conversation).where(Conversation.id == conversation.id))
    db.commit()


@router.post("/{app_id}/messages")
async def send_message(app_id: uuid.UUID, body: SendMessage, request: Request, db: DbSession):
    access = resolve_access(db, request, app_id)
    chat_request = ChatRequest(
        app_id=app_id,
        message=body.message.strip(),
        conversation_id=body.conversation_id,
        user_id=access.user.id if access.user else None,
        visitor_id=access.visitor_id,
    )
    db.close()
    return _stream(chat_request, access)


@router.post("/{app_id}/messages/{message_id}/better")
async def better_answer(app_id: uuid.UUID, message_id: int, request: Request, db: DbSession):
    """Answers the same question again one step up the model ladder, replacing this answer."""
    access = resolve_access(db, request, app_id)
    chat_request = ChatRequest(
        app_id=app_id,
        message="",
        conversation_id=None,
        user_id=access.user.id if access.user else None,
        visitor_id=access.visitor_id,
        better_than=message_id,
    )
    db.close()
    return _stream(chat_request, access)


def _stream(chat_request: ChatRequest, access: Access) -> StreamingResponse:
    async def events():
        try:
            async for event in run_chat(chat_request):
                yield f"data: {json.dumps(event)}\n\n"
        except ChatAccessError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    response = StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    _remember_visitor(response, access)
    return response


@router.post("/{app_id}/messages/{message_id}/feedback")
def rate_answer(app_id: uuid.UUID, message_id: int, body: FeedbackIn, request: Request, db: DbSession) -> MessageOut:
    """Thumbs up or down on an answer, with an optional comment."""
    access = resolve_access(db, request, app_id)
    message = db.get(Message, message_id)
    if message is None or message.role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    # Only the person who had the conversation can rate it.
    _owned_conversation(db, access, message.conversation_id)
    if status_of(message.meta) in (HELD, "discarded"):
        # The visitor never saw this draft; the reviewer's decision is its rating.
        raise HTTPException(status.HTTP_409_CONFLICT, "This answer is still being reviewed.")
    message.rating = body.rating
    message.feedback = (body.feedback or "").strip() or None
    message.rated_at = datetime.now(UTC)
    db.commit()
    return MessageOut(
        id=message.id,
        role=message.role,
        content=visible_content(message),
        blocked=bool(message.meta.get("blocked")),
        rating=message.rating,
        feedback=message.feedback,
        created_at=message.created_at,
    )
