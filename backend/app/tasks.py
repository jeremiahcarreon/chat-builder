"""Jobs run by the background worker."""

import logging
import uuid

from app.ingest import ingest_source
from app.queue import queue

log = logging.getLogger(__name__)


@queue.task(name="ping")
async def ping() -> str:
    """Trivial task proving the queue works end to end."""
    return "pong"


@queue.task(name="ingest_source", queue="ingest")
async def ingest_source_task(source_id: str) -> None:
    await ingest_source(uuid.UUID(source_id))
