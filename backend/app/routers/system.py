from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.config import get_settings
from app.deps import AdminUser, DbSession
from app.models import ServiceHeartbeat
from app.version import __version__

router = APIRouter(prefix="/api", tags=["system"])

WORKER_STALE_AFTER = timedelta(seconds=60)


class ServiceStatus(BaseModel):
    name: str
    ok: bool
    detail: str


@router.get("/health")
def health(db: DbSession) -> dict[str, str]:
    """Unauthenticated liveness check used by Docker."""
    db.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__}


def _database_status(db: DbSession) -> ServiceStatus:
    try:
        version = db.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
    except Exception as exc:  # noqa: BLE001 - report any failure in the panel
        return ServiceStatus(name="Database", ok=False, detail=str(exc)[:200])
    if version is None:
        return ServiceStatus(name="Database", ok=False, detail="pgvector extension is missing")
    return ServiceStatus(name="Database", ok=True, detail=f"Postgres with pgvector {version}")


def _worker_status(db: DbSession) -> ServiceStatus:
    beat = db.scalar(select(ServiceHeartbeat).where(ServiceHeartbeat.service == "worker"))
    if beat is None:
        return ServiceStatus(name="Background worker", ok=False, detail="Has not started yet")
    age = datetime.now(UTC) - beat.last_seen
    if age > WORKER_STALE_AFTER:
        return ServiceStatus(
            name="Background worker",
            ok=False,
            detail=f"Last seen {int(age.total_seconds())} seconds ago",
        )
    return ServiceStatus(name="Background worker", ok=True, detail="Running")


async def _embedding_status() -> ServiceStatus:
    settings = get_settings()
    name = f"Embeddings ({settings.embedding_model})"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{settings.ollama_url}/api/tags")
            response.raise_for_status()
    except httpx.HTTPError:
        return ServiceStatus(name=name, ok=False, detail="Ollama is not reachable")
    models = {m["name"].split(":")[0] for m in response.json().get("models", [])}
    if settings.embedding_model.split(":")[0] not in models:
        return ServiceStatus(
            name=name, ok=False, detail="Model is still downloading (the worker pulls it)"
        )
    return ServiceStatus(name=name, ok=True, detail="Model loaded in Ollama")


@router.get("/system/status")
async def system_status(db: DbSession, _: AdminUser) -> list[ServiceStatus]:
    return [_database_status(db), _worker_status(db), await _embedding_status()]


class BackupRequest(BaseModel):
    passphrase: str = Field(min_length=12, max_length=500)


@router.post("/system/backup")
async def full_backup(body: BackupRequest, _: AdminUser):
    """Everything in this install (apps, conversations, people, keys, documents and their
    vectors), encrypted with the passphrase. Restore with `chat-builder restore`."""
    import os
    import tempfile

    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    from starlette.concurrency import run_in_threadpool

    from app.backup import BackupError, create

    handle, path = tempfile.mkstemp(suffix=".cbbackup")

    def build() -> None:
        with os.fdopen(handle, "wb") as out:
            create(out, body.passphrase)

    try:
        await run_in_threadpool(build)
    except BackupError as exc:
        os.unlink(path)
        raise HTTPException(400, str(exc)) from None
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"chat-builder-{stamp}.cbbackup",
        background=BackgroundTask(os.unlink, path),
    )
