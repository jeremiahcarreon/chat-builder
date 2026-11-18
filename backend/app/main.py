import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.db import SessionLocal
from app.models import ChatApp, User
from app.queue import queue
from app.routers import api, apps, auth, chat, cloud_switch, embed, prices, provider_keys, quality, review, sources, system, transfer, updates, usage, users
from app.security import hash_password
from app.version import __version__

log = logging.getLogger(__name__)


def ensure_admin() -> None:
    """Create the admin account from .env on first start. Never overwrites an existing account."""
    settings = get_settings()
    email = settings.admin_email.lower()
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)) is None:
            db.add(User(email=email, password_hash=hash_password(settings.admin_password), is_admin=True))
            db.commit()
            log.info("Created admin account %s", email)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_admin()
    # Lets API requests queue background jobs.
    async with queue.open_async():
        yield


app = FastAPI(title="Chat Builder", version=__version__, lifespan=lifespan)
app.include_router(auth.router)
app.include_router(apps.router)
app.include_router(chat.router)
app.include_router(provider_keys.router)
app.include_router(cloud_switch.router)
app.include_router(sources.router)
app.include_router(system.router)
app.include_router(usage.router)
app.include_router(updates.router)
app.include_router(quality.router)
app.include_router(prices.router)
app.include_router(users.router)
app.include_router(embed.router)
app.include_router(transfer.router)
app.include_router(api.router)
app.include_router(review.router)

EMBED_FRAME = re.compile(r"^/embed/app/([0-9a-fA-F-]{36})")


@app.middleware("http")
async def frame_policy(request: Request, call_next):
    """Only the widget iframe may be embedded elsewhere, and only on the app's allowed domains."""
    response = await call_next(request)
    match = EMBED_FRAME.match(request.url.path)
    if match:
        def lookup():
            with SessionLocal() as db:
                try:
                    return embed.frame_ancestors(db.get(ChatApp, uuid.UUID(match.group(1))))
                except ValueError:
                    return embed.frame_ancestors(None)

        policy = await run_in_threadpool(lookup)
    else:
        policy = "frame-ancestors 'self'"
    response.headers["Content-Security-Policy"] = policy
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response

# Serve the built React app, with client-side routes falling back to index.html.
_static = Path(get_settings().static_dir)
if (_static / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        file = (_static / path).resolve()
        if path and file.is_file() and file.is_relative_to(_static.resolve()):
            return FileResponse(file)
        return FileResponse(_static / "index.html")
