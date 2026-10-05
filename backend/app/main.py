import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import User
from app.queue import queue
from app.version import __version__
from app.routers import apps, auth, chat, cloud_switch, prices, provider_keys, quality, sources, system, updates, usage, users
from app.security import hash_password

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
