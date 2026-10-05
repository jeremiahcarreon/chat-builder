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


@queue.task(name="run_tests", queue="tests")
async def run_tests_task(run_id: str) -> None:
    from app.evals.runner import run_tests

    await run_tests(uuid.UUID(run_id))


@queue.periodic(cron="*/10 * * * *")
@queue.task(name="review_conversations", queue="tests", queueing_lock="review_conversations")
async def review_conversations_task(timestamp: int) -> None:
    """Every 10 minutes: score conversations that have gone quiet."""
    from app.evals.review import review_conversations

    reviewed = await review_conversations()
    if reviewed:
        log.info("Reviewed %d conversation(s)", reviewed)


@queue.task(name="review_app_now", queue="tests")
async def review_app_now_task(app_id: str) -> None:
    """From the Quality tab: score this app's conversations now, without waiting for them to go quiet."""
    from datetime import timedelta

    from app.evals.review import review_conversations

    await review_conversations(uuid.UUID(app_id), quiet_for=timedelta(0))
