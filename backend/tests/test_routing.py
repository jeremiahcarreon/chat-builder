import uuid

from app.models import ChatApp
from app.routing import CloudSwitch, chat_model_for, classifier_for


def _app(**overrides) -> ChatApp:
    values = dict(id=uuid.uuid4(), owner_id=uuid.uuid4(), name="App", provider="ollama", model="qwen2.5:3b-instruct")
    values.update(overrides)
    return ChatApp(**values)


ON = CloudSwitch(enabled=True, provider="openai", chat_model="gpt-5")


def test_switch_off_keeps_local_model():
    assert chat_model_for(_app(), CloudSwitch()) == ("ollama", "qwen2.5:3b-instruct")


def test_switch_moves_local_apps_to_cloud():
    assert chat_model_for(_app(), ON) == ("openai", "gpt-5")


def test_switch_leaves_cloud_apps_alone():
    app = _app(provider="anthropic", model="claude-opus-5-5")
    assert chat_model_for(app, ON) == ("anthropic", "claude-opus-5-5")


def test_switch_without_model_does_nothing():
    assert chat_model_for(_app(), CloudSwitch(enabled=True, provider="openai")) == ("ollama", "qwen2.5:3b-instruct")


def test_classifier_follows_switch():
    app = _app()
    assert classifier_for(app, ("ollama", "qwen2.5:3b-instruct"), ON) == ("openai", "gpt-5-mini")
    with_model = CloudSwitch(enabled=True, provider="gemini", chat_model="gemini-2.5-pro", classifier_model="gemini-2.5-flash-lite")
    assert classifier_for(app, ("ollama", "x"), with_model) == ("gemini", "gemini-2.5-flash-lite")
    # A cloud classifier on a local app is left as configured.
    assert classifier_for(app, ("anthropic", "claude-haiku-4-5"), ON) == ("anthropic", "claude-haiku-4-5")
