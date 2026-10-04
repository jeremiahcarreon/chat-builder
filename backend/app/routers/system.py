from datetime import UTC, datetime, timedelta

import httpx
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, text

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.models import ServiceHeartbeat

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
    return {"status": "ok"}


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
async def system_status(db: DbSession, _: CurrentUser) -> list[ServiceStatus]:
    return [_database_status(db), _worker_status(db), await _embedding_status()]
