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
from app.models import ChatApp, Conversation, Message, User
from app.security import SESSION_COOKIE, user_for_token

router = APIRouter(prefix="/api/chat", tags=["chat"])

VISITOR_COOKIE = "cb_visitor"


@dataclass
class Access:
    app: ChatApp
    user: User | None
    visitor_id: str | None
    new_visitor: bool = False

    @property
    def is_owner(self) -> bool:
        return self.user is not None and self.app.owner_id == self.user.id

    def conversation_filter(self):
        if self.is_owner:
            return Conversation.user_id == self.user.id
        return Conversation.visitor_id == self.visitor_id


def resolve_access(db: DbSession, request: Request, app_id: uuid.UUID) -> Access:
    app = db.get(ChatApp, app_id)
    token = request.cookies.get(SESSION_COOKIE)
    user = user_for_token(db, token) if token else None
    if app is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat app not found")
    if user is not None and app.owner_id == user.id:
        return Access(app, user, None)
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
    rating: int | None = None
    feedback: str | None = None
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
        is_owner=access.is_owner,
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
            content=m.content,
            blocked=bool(m.meta.get("blocked")),
            error=bool(m.meta.get("error")) and m.meta.get("excluded", False),
            rating=m.rating,
            feedback=m.feedback,
            created_at=m.created_at,
        )
        for m in rows
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
        user_id=access.user.id if access.is_owner else None,
        visitor_id=access.visitor_id,
    )
    db.close()

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
    message.rating = body.rating
    message.feedback = (body.feedback or "").strip() or None
    message.rated_at = datetime.now(UTC)
    db.commit()
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        blocked=bool(message.meta.get("blocked")),
        rating=message.rating,
        feedback=message.feedback,
        created_at=message.created_at,
    )
