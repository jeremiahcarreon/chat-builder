"""Quality tools for an app's owner: prompt versions, rated answers, and test sets.

Every answer records the prompt version that produced it, so satisfaction and
test results can be compared across versions; regressions are cases that passed
in the previous run and fail now.
"""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select

from app.deps import AdminUser, DbSession
from app.evals.review import waiting_count
from app.evals.versions import changes, current_version
from app.models import ChatApp, Conversation, Message, PromptVersion, TestCase, TestResult, TestRun
from app.providers import JEV_PROVIDER, has_key
from app.queue import queue
from app.routers.apps import owned_app

router = APIRouter(prefix="/api/apps/{app_id}/quality", tags=["quality"])


# ----------------------------------------------------------------- versions


class VersionOut(BaseModel):
    id: int
    number: int
    created_at: datetime
    note: str
    changed: list[str]
    answers: int
    helpful: int
    not_helpful: int
    test_passed: int | None = None
    test_total: int | None = None
    satisfaction: float | None = None
    reviewed: int = 0


class VersionDetail(VersionOut):
    settings: dict
    system_prompt: str
    template_version: str


class VersionNote(BaseModel):
    note: str = Field(max_length=500)


# The prompt version of each conversation's latest answer.
_latest_answer = (
    select(Message.conversation_id, func.max(Message.id).label("message_id"))
    .where(Message.role == "assistant", Message.prompt_version_id.is_not(None))
    .group_by(Message.conversation_id)
    .subquery()
)
_conversation_version = (
    select(_latest_answer.c.conversation_id, Message.prompt_version_id.label("version_id"))
    .join(Message, Message.id == _latest_answer.c.message_id)
    .subquery()
)


def _version_rows(db: DbSession, app: ChatApp) -> list[VersionOut]:
    current_version(db, app)
    db.commit()
    versions = list(
        db.scalars(select(PromptVersion).where(PromptVersion.app_id == app.id).order_by(PromptVersion.number))
    )
    stats = {
        version_id: (answers, up, down)
        for version_id, answers, up, down in db.execute(
            select(
                Message.prompt_version_id,
                func.count(),
                func.count().filter(Message.rating == 1),
                func.count().filter(Message.rating == -1),
            )
            .where(Message.prompt_version_id.in_([v.id for v in versions]), Message.role == "assistant")
            .group_by(Message.prompt_version_id)
        )
    }
    satisfaction = {
        version_id: (avg, n)
        for version_id, avg, n in db.execute(
            select(_conversation_version.c.version_id, func.avg(Conversation.satisfaction), func.count())
            .join(Conversation, Conversation.id == _conversation_version.c.conversation_id)
            .where(Conversation.app_id == app.id, Conversation.satisfaction.is_not(None))
            .group_by(_conversation_version.c.version_id)
        )
    }
    latest_run = {}
    for run in db.scalars(
        select(TestRun)
        .where(TestRun.app_id == app.id, TestRun.status == "done")
        .order_by(TestRun.created_at)
    ):
        latest_run[run.prompt_version_id] = run
    out = []
    previous = None
    for version in versions:
        answers, up, down = stats.get(version.id, (0, 0, 0))
        run = latest_run.get(version.id)
        out.append(
            VersionOut(
                id=version.id,
                number=version.number,
                created_at=version.created_at,
                note=version.note,
                changed=changes(previous.settings if previous else None, version.settings)
                + (["prompt template"] if previous and previous.template_version != version.template_version else []),
                answers=answers,
                helpful=up,
                not_helpful=down,
                test_passed=run.passed if run else None,
                test_total=run.total if run else None,
                satisfaction=round(satisfaction[version.id][0], 1) if version.id in satisfaction else None,
                reviewed=satisfaction.get(version.id, (None, 0))[1],
            )
        )
        previous = version
    return list(reversed(out))


@router.get("/versions")
def list_versions(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> list[VersionOut]:
    return _version_rows(db, owned_app(db, user, app_id))


@router.get("/versions/{version_id}")
def get_version(app_id: uuid.UUID, version_id: int, db: DbSession, user: AdminUser) -> VersionDetail:
    app = owned_app(db, user, app_id)
    version = db.get(PromptVersion, version_id)
    if version is None or version.app_id != app.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    row = next(v for v in _version_rows(db, app) if v.id == version_id)
    return VersionDetail(
        **row.model_dump(),
        settings=version.settings,
        system_prompt=version.system_prompt,
        template_version=version.template_version,
    )


@router.patch("/versions/{version_id}")
def label_version(app_id: uuid.UUID, version_id: int, body: VersionNote, db: DbSession, user: AdminUser) -> dict:
    app = owned_app(db, user, app_id)
    version = db.get(PromptVersion, version_id)
    if version is None or version.app_id != app.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Version not found")
    version.note = body.note.strip()
    db.commit()
    return {"note": version.note}


# ----------------------------------------------------------------- feedback


class RatedAnswer(BaseModel):
    message_id: int
    conversation_id: uuid.UUID
    question: str
    answer: str
    rating: int
    feedback: str | None
    version_number: int | None
    model: str | None
    rated_at: datetime | None
    test_case_id: uuid.UUID | None


@router.get("/feedback")
def list_feedback(
    app_id: uuid.UUID,
    db: DbSession,
    user: AdminUser,
    rating: Literal["all", "helpful", "not_helpful"] = "all",
    version_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[RatedAnswer]:
    app = owned_app(db, user, app_id)
    filters = [Conversation.app_id == app.id, Message.role == "assistant", Message.rating.is_not(None)]
    if rating != "all":
        filters.append(Message.rating == (1 if rating == "helpful" else -1))
    if version_id is not None:
        filters.append(Message.prompt_version_id == version_id)
    rows = db.execute(
        select(Message, PromptVersion.number)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .outerjoin(PromptVersion, PromptVersion.id == Message.prompt_version_id)
        .where(and_(*filters))
        .order_by(Message.rated_at.desc().nulls_last(), Message.id.desc())
        .limit(limit)
    ).all()
    message_ids = [m.id for m, _ in rows]
    # The question is the user message right before each answer.
    questions = {}
    for message, _ in rows:
        questions[message.id] = db.scalar(
            select(Message.content)
            .where(Message.conversation_id == message.conversation_id, Message.id < message.id, Message.role == "user")
            .order_by(Message.id.desc())
            .limit(1)
        ) or ""
    cases = dict(
        db.execute(
            select(TestCase.source_message_id, TestCase.id).where(TestCase.source_message_id.in_(message_ids))
        ).all()
    ) if message_ids else {}
    return [
        RatedAnswer(
            message_id=m.id,
            conversation_id=m.conversation_id,
            question=questions[m.id],
            answer=m.content,
            rating=m.rating,
            feedback=m.feedback,
            version_number=number,
            model=m.meta.get("model"),
            rated_at=m.rated_at,
            test_case_id=cases.get(m.id),
        )
        for m, number in rows
    ]


# --------------------------------------------------------------- test cases


class CaseIn(BaseModel):
    question: str = Field(min_length=1, max_length=8_000)
    expectation: Literal["answer", "refuse"] = "answer"
    must_include: str = Field(default="", max_length=4_000)
    reference_answer: str = Field(default="", max_length=20_000)
    notes: str = Field(default="", max_length=4_000)


class CaseOut(CaseIn):
    id: uuid.UUID
    source_message_id: int | None
    created_at: datetime
    last_passed: bool | None = None


class CaseFromAnswer(BaseModel):
    expectation: Literal["answer", "refuse"] = "answer"


def _owned_case(db: DbSession, app: ChatApp, case_id: uuid.UUID) -> TestCase:
    test_case = db.get(TestCase, case_id)
    if test_case is None or test_case.app_id != app.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Test case not found")
    return test_case


def _case_out(test_case: TestCase, last_passed: bool | None = None) -> CaseOut:
    return CaseOut(
        id=test_case.id,
        question=test_case.question,
        expectation=test_case.expectation,
        must_include=test_case.must_include,
        reference_answer=test_case.reference_answer,
        notes=test_case.notes,
        source_message_id=test_case.source_message_id,
        created_at=test_case.created_at,
        last_passed=last_passed,
    )


@router.get("/tests")
def list_cases(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> list[CaseOut]:
    app = owned_app(db, user, app_id)
    last_run = db.scalar(
        select(TestRun.id).where(TestRun.app_id == app.id, TestRun.status == "done").order_by(TestRun.created_at.desc()).limit(1)
    )
    results = dict(db.execute(select(TestResult.case_id, TestResult.passed).where(TestResult.run_id == last_run)).all()) if last_run else {}
    cases = db.scalars(select(TestCase).where(TestCase.app_id == app.id).order_by(TestCase.created_at))
    return [_case_out(c, results.get(c.id)) for c in cases]


@router.post("/tests", status_code=status.HTTP_201_CREATED)
def create_case(app_id: uuid.UUID, body: CaseIn, db: DbSession, user: AdminUser) -> CaseOut:
    app = owned_app(db, user, app_id)
    test_case = TestCase(app_id=app.id, **body.model_dump())
    db.add(test_case)
    db.commit()
    return _case_out(test_case)


@router.post("/tests/from-answer/{message_id}", status_code=status.HTTP_201_CREATED)
def case_from_answer(app_id: uuid.UUID, message_id: int, body: CaseFromAnswer, db: DbSession, user: AdminUser) -> CaseOut:
    """Turns a rated answer into a test case. A helpful answer becomes the reference answer."""
    app = owned_app(db, user, app_id)
    message = db.get(Message, message_id)
    conversation = db.get(Conversation, message.conversation_id) if message else None
    if message is None or conversation is None or conversation.app_id != app.id or message.role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
    question = db.scalar(
        select(Message.content)
        .where(Message.conversation_id == message.conversation_id, Message.id < message.id, Message.role == "user")
        .order_by(Message.id.desc())
        .limit(1)
    )
    if not question:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Couldn't find the question for this answer")
    helpful = message.rating == 1
    test_case = TestCase(
        app_id=app.id,
        question=question,
        expectation=body.expectation,
        reference_answer=message.content if helpful and body.expectation == "answer" else "",
        notes=(f"From user feedback: {message.feedback}" if message.feedback else
               "From a helpful answer" if helpful else "From an unhelpful answer"),
        source_message_id=message.id,
    )
    db.add(test_case)
    db.commit()
    return _case_out(test_case)


@router.put("/tests/{case_id}")
def update_case(app_id: uuid.UUID, case_id: uuid.UUID, body: CaseIn, db: DbSession, user: AdminUser) -> CaseOut:
    app = owned_app(db, user, app_id)
    test_case = _owned_case(db, app, case_id)
    for key, value in body.model_dump().items():
        setattr(test_case, key, value)
    db.commit()
    return _case_out(test_case)


@router.delete("/tests/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case(app_id: uuid.UUID, case_id: uuid.UUID, db: DbSession, user: AdminUser) -> None:
    app = owned_app(db, user, app_id)
    db.delete(_owned_case(db, app, case_id))
    db.commit()


# ---------------------------------------------------------------- test runs


class RunOut(BaseModel):
    id: uuid.UUID
    status: str
    error: str | None
    version_number: int | None
    total: int
    passed: int
    completed: int
    regressions: int
    fixed: int
    created_at: datetime
    finished_at: datetime | None


class ResultOut(BaseModel):
    case_id: uuid.UUID
    question: str
    expectation: str
    passed: bool
    blocked: bool
    answer: str
    failures: list[str]
    seconds: float | None
    # Compared with the previous completed run: "regressed", "fixed", "new" or "same".
    change: str


def _previous_run(db: DbSession, run: TestRun) -> TestRun | None:
    return db.scalar(
        select(TestRun)
        .where(TestRun.app_id == run.app_id, TestRun.status == "done", TestRun.created_at < run.created_at)
        .order_by(TestRun.created_at.desc())
        .limit(1)
    )


def _outcomes(db: DbSession, run_id: uuid.UUID | None) -> dict[uuid.UUID, bool]:
    if run_id is None:
        return {}
    return dict(db.execute(select(TestResult.case_id, TestResult.passed).where(TestResult.run_id == run_id)).all())


def _change(now: bool, before: bool | None) -> str:
    if before is None:
        return "new"
    if before and not now:
        return "regressed"
    if not before and now:
        return "fixed"
    return "same"


def _run_out(db: DbSession, run: TestRun) -> RunOut:
    version_number = db.scalar(select(PromptVersion.number).where(PromptVersion.id == run.prompt_version_id))
    current = _outcomes(db, run.id)
    previous_run = _previous_run(db, run)
    previous = _outcomes(db, previous_run.id if previous_run else None)
    changes_ = [_change(passed, previous.get(case_id)) for case_id, passed in current.items()]
    return RunOut(
        id=run.id,
        status=run.status,
        error=run.error,
        version_number=version_number,
        total=run.total,
        passed=run.passed,
        completed=len(current),
        regressions=changes_.count("regressed"),
        fixed=changes_.count("fixed"),
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def _owned_run(db: DbSession, app: ChatApp, run_id: uuid.UUID) -> TestRun:
    run = db.get(TestRun, run_id)
    if run is None or run.app_id != app.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Test run not found")
    return run


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def start_run(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> RunOut:
    app = owned_app(db, user, app_id)
    count = db.scalar(select(func.count()).select_from(TestCase).where(TestCase.app_id == app.id))
    if not count:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add some test cases first.")
    busy = db.scalar(
        select(TestRun.id).where(TestRun.app_id == app.id, TestRun.status.in_(("queued", "running"))).limit(1)
    )
    if busy:
        raise HTTPException(status.HTTP_409_CONFLICT, "A test run is already in progress.")
    version = current_version(db, app)
    run = TestRun(app_id=app.id, prompt_version_id=version.id, total=count)
    db.add(run)
    db.commit()
    await queue.configure_task("run_tests", queue="tests").defer_async(run_id=str(run.id))
    return _run_out(db, run)


@router.get("/runs")
def list_runs(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> list[RunOut]:
    app = owned_app(db, user, app_id)
    runs = db.scalars(select(TestRun).where(TestRun.app_id == app.id).order_by(TestRun.created_at.desc()).limit(50))
    return [_run_out(db, run) for run in runs]


@router.get("/runs/{run_id}")
def run_results(app_id: uuid.UUID, run_id: uuid.UUID, db: DbSession, user: AdminUser) -> list[ResultOut]:
    app = owned_app(db, user, app_id)
    run = _owned_run(db, app, run_id)
    previous_run = _previous_run(db, run)
    previous = _outcomes(db, previous_run.id if previous_run else None)
    rows = db.execute(
        select(TestResult, TestCase)
        .join(TestCase, TestCase.id == TestResult.case_id)
        .where(TestResult.run_id == run.id)
        # Regressions and failures first.
        .order_by(case((TestResult.passed.is_(False), 0), else_=1), TestCase.created_at)
    ).all()
    results = [
        ResultOut(
            case_id=test_case.id,
            question=test_case.question,
            expectation=test_case.expectation,
            passed=result.passed,
            blocked=result.blocked,
            answer=result.answer,
            failures=result.failures,
            seconds=result.seconds,
            change=_change(result.passed, previous.get(test_case.id)),
        )
        for result, test_case in rows
    ]
    order = {"regressed": 0, "new": 1, "same": 2, "fixed": 3}
    return sorted(results, key=lambda r: (r.passed, order[r.change]))


# ------------------------------------------------------------ satisfaction


class ReviewSummary(BaseModel):
    # False when the owner has no TypeSafe key (reviews need Jev).
    available: bool
    reviewed: int
    waiting: int
    average: float | None
    resolved_rate: float | None


class ReviewedConversation(BaseModel):
    id: uuid.UUID
    title: str
    satisfaction: float
    resolved: float | None
    messages: int
    version_number: int | None
    reviewed_at: datetime
    updated_at: datetime


class TranscriptMessage(BaseModel):
    role: str
    content: str
    blocked: bool
    rating: int | None
    feedback: str | None
    created_at: datetime


@router.get("/reviews")
def review_summary(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> ReviewSummary:
    app = owned_app(db, user, app_id)
    avg, reviewed, resolved = db.execute(
        select(func.avg(Conversation.satisfaction), func.count(), func.avg(Conversation.resolved))
        .where(Conversation.app_id == app.id, Conversation.satisfaction.is_not(None))
    ).one()
    return ReviewSummary(
        available=has_key(db, user.id, JEV_PROVIDER),
        reviewed=reviewed,
        waiting=waiting_count(db, app.id),
        average=round(avg, 1) if avg is not None else None,
        resolved_rate=round(resolved, 3) if resolved is not None else None,
    )


@router.get("/reviews/conversations")
def reviewed_conversations(
    app_id: uuid.UUID,
    db: DbSession,
    user: AdminUser,
    order: Literal["lowest", "highest", "recent"] = "lowest",
    limit: int = Query(25, ge=1, le=200),
) -> list[ReviewedConversation]:
    app = owned_app(db, user, app_id)
    counts = select(Message.conversation_id, func.count().label("n")).group_by(Message.conversation_id).subquery()
    sort = {
        "lowest": Conversation.satisfaction.asc(),
        "highest": Conversation.satisfaction.desc(),
        "recent": Conversation.reviewed_at.desc(),
    }[order]
    rows = db.execute(
        select(Conversation, counts.c.n, PromptVersion.number)
        .join(counts, counts.c.conversation_id == Conversation.id)
        .outerjoin(_conversation_version, _conversation_version.c.conversation_id == Conversation.id)
        .outerjoin(PromptVersion, PromptVersion.id == _conversation_version.c.version_id)
        .where(Conversation.app_id == app.id, Conversation.satisfaction.is_not(None))
        .order_by(sort)
        .limit(limit)
    ).all()
    return [
        ReviewedConversation(
            id=c.id,
            title=c.title,
            satisfaction=c.satisfaction,
            resolved=c.resolved,
            messages=n,
            version_number=number,
            reviewed_at=c.reviewed_at,
            updated_at=c.updated_at,
        )
        for c, n, number in rows
    ]


@router.get("/reviews/conversations/{conversation_id}")
def conversation_transcript(
    app_id: uuid.UUID, conversation_id: uuid.UUID, db: DbSession, user: AdminUser
) -> list[TranscriptMessage]:
    """The full conversation, for the app owner reviewing quality (including visitors' chats)."""
    app = owned_app(db, user, app_id)
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.app_id != app.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.role.in_(("user", "assistant")))
        .order_by(Message.id)
    )
    return [
        TranscriptMessage(
            role=m.role,
            content=m.content,
            blocked=bool(m.meta.get("blocked")),
            rating=m.rating,
            feedback=m.feedback,
            created_at=m.created_at,
        )
        for m in rows
    ]


@router.post("/reviews/run", status_code=status.HTTP_202_ACCEPTED)
async def review_now(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> dict:
    """Scores this app's conversations now, without waiting for them to go quiet."""
    app = owned_app(db, user, app_id)
    if not has_key(db, user.id, JEV_PROVIDER):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Satisfaction reviews use Jev. Add a TypeSafe key in Settings.")
    await queue.configure_task("review_app_now", queue="tests").defer_async(app_id=str(app.id))
    return {"queued": True}
