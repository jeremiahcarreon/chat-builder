"""Answers held for review: who is held, what the visitor sees, and what a decision records."""

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app import review
from app.llm import engine
from app.models import Message


def _req(user_id=None, external_id=None):
    return engine.ChatRequest(app_id=uuid.uuid4(), message="Does CC161 fit?", conversation_id=None,
                              user_id=user_id, visitor_id="v1", external_id=external_id)


def test_only_anonymous_visitors_on_a_reviewing_app_are_held():
    reviewing = SimpleNamespace(review_answers=True)
    assert review.holds_answer(reviewing, _req())
    assert not review.holds_answer(reviewing, _req(user_id=uuid.uuid4())), "signed-in staff get answers live"
    assert not review.holds_answer(reviewing, _req(external_id="ebay-1")), "API callers review on their side"
    assert not review.holds_answer(SimpleNamespace(review_answers=False), _req())


def _held(content="It fits a 2008 Silverado."):
    return Message(id=1, conversation_id=uuid.uuid4(), role="assistant", content=content,
                   meta={"review": {"status": review.HELD}, "tool_calls": []}, created_at=datetime.now(UTC))


def test_the_visitor_sees_a_notice_until_the_draft_is_sent():
    message = _held()
    assert review.visible_content(message) == review.HELD_NOTICE
    assert review.not_shown(message.meta)

    review.decide(message, "sent", None, SimpleNamespace(email="rev@ccs.test"))

    assert review.visible_content(message) == "It fits a 2008 Silverado."
    assert not review.not_shown(message.meta)
    assert message.meta["review"]["by"] == "rev@ccs.test"
    assert message.rating == 1


def test_an_edit_replaces_the_answer_and_keeps_the_draft():
    message = _held()
    changed = review.decide(message, "edited", "  It fits the 4.3L only.  ", SimpleNamespace(email="r@x"))
    assert changed == "It fits the 4.3L only."
    assert message.content == "It fits the 4.3L only."
    assert message.meta["review"]["original"] == "It fits a 2008 Silverado."
    assert message.rating == -1


def test_an_edit_that_changes_nothing_counts_as_sent():
    message = _held()
    assert review.decide(message, "edited", "It fits a 2008 Silverado.", SimpleNamespace(email="r@x")) is None
    assert message.meta["review"]["status"] == "sent"


def test_a_discarded_draft_is_never_shown():
    message = _held()
    review.decide(message, "discarded", None, SimpleNamespace(email="r@x"))
    assert review.visible_content(message) == review.DISCARDED_NOTICE
    assert review.not_shown(message.meta)


def test_a_decision_is_made_once_and_an_edit_needs_text():
    message = _held()
    with pytest.raises(review.ReviewError):
        review.decide(message, "edited", "   ", SimpleNamespace(email="r@x"))
    review.decide(message, "sent", None, SimpleNamespace(email="r@x"))
    with pytest.raises(review.ReviewError):
        review.decide(message, "discarded", None, SimpleNamespace(email="r@x"))


def _run(monkeypatch, req, review_answers=True):
    app = SimpleNamespace(id=req.app_id, review_answers=review_answers, auto_route=False)
    prep = engine.Prepared(app=app, conversation_id=uuid.uuid4(), user_message_id=1, history=[],
                           client=object(), provider="p", model="m", question=req.message)
    saved = {}

    async def generate(app, client, provider, model, history, question, gen, emit, **kwargs):
        await emit({"type": "tool", "label": "Checking the catalog"})
        await emit({"type": "text", "text": "It fits."})
        gen.parts.append("It fits.")
        return gen

    async def allowed(*args):
        return SimpleNamespace(allowed=True, usage=[], meta={})

    def finish(prep, answer, meta, exclude=False):
        saved.update(answer=answer, meta=meta)
        return 7

    monkeypatch.setattr(engine, "_prepare", lambda req: prep)
    monkeypatch.setattr(engine, "check_message", allowed)
    monkeypatch.setattr(engine, "generate", generate)
    monkeypatch.setattr(engine, "_finish", finish)

    async def collect():
        return [event async for event in engine.run_chat(req)]

    return asyncio.run(collect()), saved


def test_a_held_answer_streams_progress_but_not_the_draft(monkeypatch):
    events, saved = _run(monkeypatch, _req())
    types = [e["type"] for e in events]
    assert "text" not in types
    assert "tool" in types, "the visitor still sees that it is working"
    assert {"type": "held", "message": review.HELD_NOTICE} in events
    assert saved["answer"] == "It fits."
    assert saved["meta"]["review"] == {"status": review.HELD}


def test_signed_in_people_get_the_answer_live(monkeypatch):
    events, saved = _run(monkeypatch, _req(user_id=uuid.uuid4()))
    assert {"type": "text", "text": "It fits."} in events
    assert "review" not in saved["meta"]
