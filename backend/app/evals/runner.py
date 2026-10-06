"""Runs an app's test set against its current prompt version (a background job)."""

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.db import SessionLocal
from app.evals.grading import grade
from app.guardrails.classifier import ClassifierError
from app.guardrails.jev import JevClient
from app.guardrails.pipeline import effective_classifier
from app.llm.clients import client_for
from app.llm.engine import answer_once
from app.models import ChatApp, TestCase, TestResult, TestRun, UsageEvent
from app.providers import JEV_PROVIDER, ProviderError, get_api_key

log = logging.getLogger(__name__)

# Cases answered at the same time; local models are usually the bottleneck.
CONCURRENCY = 2


def _load(run_id: uuid.UUID):
    with SessionLocal() as db:
        run = db.get(TestRun, run_id)
        if run is None:
            return None, None, []
        app = db.get(ChatApp, run.app_id)
        cases = list(db.scalars(select(TestCase).where(TestCase.app_id == app.id).order_by(TestCase.created_at)))
        run.status = "running"
        run.total = len(cases)
        run.passed = 0
        db.commit()
        db.expunge_all()
        return run, app, cases


def _judge_client(app: ChatApp):
    """The judge uses the same small, fast model as the classifier (Jev, when available)."""
    with SessionLocal() as db:
        try:
            provider, model = effective_classifier(db, app)
            if provider == JEV_PROVIDER:
                return JevClient(get_api_key(db, app.owner_id, provider)), provider, model
            return client_for(db, app.owner_id, provider), provider, model
        except (ProviderError, ClassifierError):
            return None, "", ""


def _save_result(run_id: uuid.UUID, case_id: uuid.UUID, passed: bool, blocked: bool, answer: str,
                 failures: list[str], seconds: float, usage: list[UsageEvent]) -> None:
    with SessionLocal() as db:
        db.add(TestResult(run_id=run_id, case_id=case_id, passed=passed, blocked=blocked,
                          answer=answer, failures=failures, seconds=round(seconds, 2)))
        for event in usage:
            db.add(event)
        if passed:
            # Atomic: cases finish concurrently, and a read-modify-write would lose counts.
            db.execute(update(TestRun).where(TestRun.id == run_id).values(passed=TestRun.passed + 1))
        db.commit()


def _finish(run_id: uuid.UUID, status: str, error: str | None = None) -> None:
    with SessionLocal() as db:
        run = db.get(TestRun, run_id)
        run.status = status
        run.error = error
        run.finished_at = datetime.now(UTC)
        db.commit()


async def run_tests(run_id: uuid.UUID) -> None:
    run, app, cases = await asyncio.to_thread(_load, run_id)
    if run is None:
        return
    try:
        judge_client, judge_provider, judge_model = await asyncio.to_thread(_judge_client, app)
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def one(case: TestCase) -> None:
            async with semaphore:
                started = time.monotonic()
                result = await answer_once(app, case.question, tier=run.tier)
                graded = await grade(app, case, result.text, result.blocked, result.meta, judge_client, judge_model)
                usage = list(result.usage)
                if graded.judge_input_tokens or graded.judge_output_tokens:
                    usage.append(UsageEvent(app_id=app.id, kind="test", provider=judge_provider, model=judge_model,
                                            input_tokens=graded.judge_input_tokens,
                                            output_tokens=graded.judge_output_tokens))
                await asyncio.to_thread(_save_result, run_id, case.id, graded.passed, result.blocked,
                                        result.text, graded.failures, time.monotonic() - started, usage)

        await asyncio.gather(*(one(case) for case in cases))
    except Exception as exc:
        log.exception("Test run %s failed", run_id)
        await asyncio.to_thread(_finish, run_id, "failed", str(exc)[:500])
        return
    await asyncio.to_thread(_finish, run_id, "done")
