from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.security import (
    SESSION_COOKIE,
    authenticate,
    create_session,
    end_session,
    login_throttle,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    email: str
    name: str = ""
    is_admin: bool


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: DbSession) -> UserOut:
    ip = request.client.host if request.client else "unknown"
    if login_throttle.is_blocked(ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts. Try again later."
        )
    user = authenticate(db, body.email, body.password)
    if user is None:
        login_throttle.record_failure(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    login_throttle.reset(ip)

    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        create_session(db, user),
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return UserOut(email=user.email, name=user.name, is_admin=user.is_admin)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: DbSession) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        end_session(db, token)
    response.delete_cookie(SESSION_COOKIE)


@router.get("/me")
def me(user: CurrentUser) -> UserOut:
    return UserOut(email=user.email, name=user.name, is_admin=user.is_admin)
