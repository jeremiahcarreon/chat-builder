import uuid

import pytest

from app.lessons import LESSONS_NAME, Lesson, lesson_model, lesson_prompt, parse_lesson, scrub
from app.llm.base import LLMError
from app.models import ChatApp


def _app(**overrides) -> ChatApp:
    values = dict(
        id=uuid.uuid4(), owner_id=uuid.uuid4(), name="eBay Messages Helper", description="Drafts eBay replies",
        provider="anthropic", model="claude-sonnet-5-5", classifier_provider=None, classifier_model=None,
    )
    values.update(overrides)
    return ChatApp(**values)


def test_lesson_text_has_the_three_parts():
    lesson = Lesson("A buyer asks for a polished finish.", "We say we don't offer polishing.", "No polishing service.")
    assert lesson.text == (
        "Situation: A buyer asks for a polished finish.\n"
        "How we reply: We say we don't offer polishing.\n"
        "Why: No polishing service."
    )
    assert "Why" not in Lesson("s", "r").text


def test_parse_lesson_keeps_or_drops():
    kept = parse_lesson('Here: {"keep": true, "situation": "Fitment question", "reply": "Ask for the engine", "why": ""}')
    assert kept == Lesson("Fitment question", "Ask for the engine", "")
    assert parse_lesson('{"keep": false, "situation": "thanks", "reply": "you are welcome"}') is None
    assert parse_lesson('{"keep": true, "situation": "", "reply": "x"}') is None
    with pytest.raises(LLMError):
        parse_lesson("no json")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Email jdoe@example.com for help", "Email [email] for help"),
        ("Order 12-34567-89012 shipped", "Order [order] shipped"),
        ("Call (555) 123-4567 today", "Call [phone] today"),
        ("Tracking 1Z999 and item #225231072086 and order 521165", "Tracking 1Z999 and item [number] and order [number]"),
        ("CC289 fits a 1967 Impala with a 3 row core", "CC289 fits a 1967 Impala with a 3 row core"),
    ],
)
def test_scrub_removes_identifiers_but_keeps_part_details(text, expected):
    assert scrub(text) == expected


def test_prompt_marks_the_exchange_as_data_and_shows_the_changed_draft():
    prompt = lesson_prompt("Does it come polished?", "We don't offer polishing.", draft="Yes, polished is available.",
                           context={"ebay_buyer": "other_buyer"})
    assert "<customer_message>" in prompt and "<reply_sent" in prompt and "<assistant_draft" in prompt
    assert "ebay_buyer: other_buyer" in prompt
    # An unchanged draft adds nothing.
    assert "<assistant_draft" not in lesson_prompt("q", "same", draft="same")


class _Db:
    def scalar(self, *_):
        return None

    def execute(self, *_):
        class _R:
            def first(self):
                return None

            def scalars(self):
                return []
        return _R()


def test_lesson_writer_is_the_classifier_model_but_never_jev(monkeypatch):
    import app.lessons as lessons

    monkeypatch.setattr(lessons, "load_cloud_switch", lambda db, owner: type("S", (), {"active": False})())
    assert lesson_model(_Db(), _app()) == ("anthropic", "claude-haiku-4-5")
    assert lesson_model(_Db(), _app(classifier_provider="typesafe", classifier_model="jev-latest")) == ("anthropic", "claude-haiku-4-5")
    assert lesson_model(_Db(), _app(classifier_provider="openai", classifier_model="gpt-5-mini")) == ("openai", "gpt-5-mini")


def test_source_name():
    assert LESSONS_NAME == "Learned from sent replies"
