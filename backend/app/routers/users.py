"""Workspace people: admins invite members, choose which apps they can use, and
can block anyone. Invites and password resets are one-time links the admin
shares (Chat Builder doesn't send email)."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.deps import AdminUser, CurrentUser, DbSession
from app.models import AppAccess, ChatApp, User, UserSession
from app.security import SESSION_COOKIE, create_session, hash_password

router = APIRouter(prefix="/api", tags=["users"])

INVITE_VALID_FOR = timedelta(days=7)
MIN_PASSWORD = 12


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    username: str | None
    is_admin: bool
    source: str
    blocked: bool
    has_password: bool
    invite_pending: bool
    app_ids: list[uuid.UUID]
    last_seen_at: datetime | None
    created_at: datetime


class UserIn(BaseModel):
    email: EmailStr
    name: str = Field(default="", max_length=200)
    is_admin: bool = False
    app_ids: list[uuid.UUID] = []


class UserUpdate(BaseModel):
    name: str = Field(default="", max_length=200)
    is_admin: bool
    blocked: bool
    app_ids: list[uuid.UUID]


class InviteLink(BaseModel):
    user: UserOut
    link: str
    expires_at: datetime


def public_base(request: Request) -> str:
    """The address people reach Chat Builder at (PUBLIC_URL, or the request's own)."""
    configured = get_settings().public_url
    return (configured or str(request.base_url)).rstrip("/")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _out(db: DbSession, user: User) -> UserOut:
    app_ids = list(db.scalars(select(AppAccess.app_id).where(AppAccess.user_id == user.id)))
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        username=user.username,
        is_admin=user.is_admin,
        source=user.source,
        blocked=user.blocked,
        has_password=user.password_hash is not None,
        invite_pending=user.invite_token_hash is not None
        and user.invite_expires_at is not None
        and user.invite_expires_at > datetime.now(UTC),
        app_ids=app_ids,
        last_seen_at=user.last_seen_at,
        created_at=user.created_at,
    )


def _issue_link(db: DbSession, request: Request, user: User) -> InviteLink:
    token = secrets.token_urlsafe(32)
    user.invite_token_hash = _hash(token)
    user.invite_expires_at = datetime.now(UTC) + INVITE_VALID_FOR
    db.commit()
    return InviteLink(user=_out(db, user), link=f"{public_base(request)}/invite/{token}", expires_at=user.invite_expires_at)


def _set_access(db: DbSession, user: User, app_ids: list[uuid.UUID]) -> None:
    existing = set(db.scalars(select(ChatApp.id).where(ChatApp.id.in_(app_ids)))) if app_ids else set()
    db.execute(delete(AppAccess).where(AppAccess.user_id == user.id))
    db.add_all(AppAccess(app_id=app_id, user_id=user.id) for app_id in existing)


def _other_active_admins(db: DbSession, user: User) -> int:
    return db.scalar(
        select(func.count()).select_from(User).where(
            User.is_admin.is_(True), User.blocked.is_(False), User.id != user.id
        )
    ) or 0


@router.get("/users")
def list_users(db: DbSession, _: AdminUser) -> list[UserOut]:
    users = db.scalars(select(User).order_by(User.is_admin.desc(), User.created_at))
    return [_out(db, u) for u in users]


@router.post("/users", status_code=status.HTTP_201_CREATED)
def invite_user(body: UserIn, request: Request, db: DbSession, _: AdminUser) -> InviteLink:
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Someone with that email already exists.")
    user = User(email=email, name=body.name.strip(), is_admin=body.is_admin, source="local")
    db.add(user)
    db.flush()
    _set_access(db, user, body.app_ids)
    return _issue_link(db, request, user)


@router.put("/users/{user_id}")
def update_user(user_id: uuid.UUID, body: UserUpdate, db: DbSession, admin: AdminUser) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Person not found")
    losing_admin = user.is_admin and (not body.is_admin or body.blocked)
    if user.id == admin.id and losing_admin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't remove your own admin access or block yourself.")
    if losing_admin and not _other_active_admins(db, user):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There must be at least one active admin.")
    user.name = body.name.strip()
    user.is_admin = body.is_admin
    user.blocked = body.blocked
    _set_access(db, user, body.app_ids)
    if body.blocked:
        # Signs them out everywhere.
        db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    return _out(db, user)


@router.post("/users/{user_id}/reset")
def reset_link(user_id: uuid.UUID, request: Request, db: DbSession, _: AdminUser) -> InviteLink:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Person not found")
    if user.source == "embed" and user.password_hash is None and not user.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This person signs in through an embedding site.")
    return _issue_link(db, request, user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: uuid.UUID, db: DbSession, admin: AdminUser) -> None:
    user = db.get(User, user_id)
    if user is None:
        return
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't remove yourself.")
    if user.is_admin and not _other_active_admins(db, user):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "There must be at least one active admin.")
    db.delete(user)
    db.commit()


# ------------------------------------------------------------------ invites


class InviteInfo(BaseModel):
    email: str
    name: str
    reset: bool


class AcceptInvite(BaseModel):
    password: str = Field(min_length=MIN_PASSWORD, max_length=200)


def _invited(db: DbSession, token: str) -> User:
    user = db.scalar(select(User).where(User.invite_token_hash == _hash(token)))
    if user is None or user.blocked or user.invite_expires_at is None or user.invite_expires_at < datetime.now(UTC):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This link is invalid or has expired. Ask an admin for a new one.")
    return user


@router.get("/invites/{token}")
def invite_info(token: str, db: DbSession) -> InviteInfo:
    user = _invited(db, token)
    return InviteInfo(email=user.email, name=user.name, reset=user.password_hash is not None)


@router.post("/invites/{token}")
def accept_invite(token: str, body: AcceptInvite, response: Response, db: DbSession) -> dict:
    user = _invited(db, token)
    user.password_hash = hash_password(body.password)
    user.invite_token_hash = None
    user.invite_expires_at = None
    # A password reset signs out every other session.
    db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    db.commit()
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        create_session(db, user),
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return {"email": user.email}


# ------------------------------------------------------------------ my apps


class MyApp(BaseModel):
    id: uuid.UUID
    name: str
    description: str


@router.get("/my/apps")
def my_apps(db: DbSession, user: CurrentUser) -> list[MyApp]:
    """The chat apps this person can use."""
    query = select(ChatApp).order_by(ChatApp.name)
    if not user.is_admin:
        query = query.join(AppAccess, AppAccess.app_id == ChatApp.id).where(AppAccess.user_id == user.id)
    return [MyApp(id=a.id, name=a.name, description=a.description) for a in db.scalars(query)]
