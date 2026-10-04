import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User, UserSession

SESSION_COOKIE = "cb_session"

_hasher = PasswordHasher()
# A real hash to verify against when the email doesn't exist, so response
# timing doesn't reveal which emails have accounts.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        verify_password(_DUMMY_HASH, password)
        return None
    return user if verify_password(user.password_hash, password) else None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            token_hash=_hash_token(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(days=get_settings().session_days),
        )
    )
    db.execute(delete(UserSession).where(UserSession.expires_at < datetime.now(UTC)))
    db.commit()
    return token


def user_for_token(db: Session, token: str) -> User | None:
    session = db.get(UserSession, _hash_token(token))
    if session is None or session.expires_at < datetime.now(UTC):
        return None
    return db.get(User, session.user_id)


def end_session(db: Session, token: str) -> None:
    db.execute(delete(UserSession).where(UserSession.token_hash == _hash_token(token)))
    db.commit()


class LoginThrottle:
    """Blocks an IP after too many failed logins in a time window (in-memory)."""

    def __init__(self, max_failures: int = 10, window_seconds: int = 900):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, ip: str) -> deque[float]:
        attempts = self._failures[ip]
        cutoff = time.monotonic() - self.window
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts

    def is_blocked(self, ip: str) -> bool:
        return len(self._prune(ip)) >= self.max_failures

    def record_failure(self, ip: str) -> None:
        self._prune(ip).append(time.monotonic())

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)


login_throttle = LoginThrottle()
