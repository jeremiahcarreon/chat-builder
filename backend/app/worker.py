"""Background worker: runs queued jobs (document ingestion from step 3 on).

Also reports a heartbeat for the status panel and makes sure the embedding
model is downloaded into Ollama.
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx
import procrastinate
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.db import SessionLocal
from app.models import ServiceHeartbeat

log = logging.getLogger("worker")

HEARTBEAT_SECONDS = 15


def _conninfo() -> str:
    # Procrastinate takes a plain libpq URL, not SQLAlchemy's "+psycopg" form.
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://", 1)


app = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=_conninfo()))


@app.task(name="ping")
async def ping() -> str:
    """Trivial task proving the queue works end to end."""
    return "pong"


def _beat() -> None:
    with SessionLocal() as db:
        stmt = insert(ServiceHeartbeat).values(
            service="worker", detail={}, last_seen=datetime.now(UTC)
        )
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["service"], set_={"last_seen": stmt.excluded.last_seen}
            )
        )
        db.commit()


async def heartbeat() -> None:
    while True:
        try:
            await asyncio.to_thread(_beat)
        except Exception:
            log.exception("Heartbeat failed")
        await asyncio.sleep(HEARTBEAT_SECONDS)


async def ensure_embedding_model() -> None:
    """Pull the embedding model into Ollama if it isn't there yet. Retries until it succeeds."""
    settings = get_settings()
    while True:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                tags = (await client.get(f"{settings.ollama_url}/api/tags")).json()
                names = {m["name"].split(":")[0] for m in tags.get("models", [])}
                if settings.embedding_model.split(":")[0] in names:
                    log.info("Embedding model %s is available", settings.embedding_model)
                    return
                log.info("Pulling embedding model %s ...", settings.embedding_model)
                response = await client.post(
                    f"{settings.ollama_url}/api/pull",
                    json={"model": settings.embedding_model, "stream": False},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Could not pull embedding model yet (%s); retrying in 15s", exc)
            await asyncio.sleep(15)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    background = [asyncio.create_task(heartbeat()), asyncio.create_task(ensure_embedding_model())]
    async with app.open_async():
        await app.run_worker_async()
    for task in background:
        task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
