import uuid

from app.models import ChatApp
from app.routers.transfer import ALWAYS_COPIED, SETTING_GROUPS, copied_settings


def _app() -> ChatApp:
    return ChatApp(
        id=uuid.uuid4(), owner_id=uuid.uuid4(), name="Sales Helper", description="Helps reps", welcome_message="Hi",
        provider="anthropic", model="claude-sonnet-5-5", personality="Friendly", knowledge_notes="SKUs start with CC",
        in_scope="parts, fitment", out_of_scope="pricing disputes", refusal_message="Can't help with that",
        strict_mode=False, classifier_enabled=False, classifier_strictness="lenient", classifier_fail_open=False,
        temperature=0.2, seed=7, max_answer_tokens=2048, messages_per_minute=30, daily_token_limit=900_000,
        auto_route=True, route_tiers=[{"provider": "anthropic", "model": "claude-haiku-5-5", "efforts": [], "use_for": ""}],
        router="llm",
    )


def test_every_setting_is_in_exactly_one_group():
    from app.routers.apps import AppIn

    grouped = [name for group in SETTING_GROUPS for name in group["fields"]]
    assert len(grouped) == len(set(grouped))
    covered = set(grouped) | set(ALWAYS_COPIED) | {"name", "is_public"}
    assert covered == set(AppIn.model_fields), "A new app setting needs a copy group (or must be deliberately left out)"


def test_copy_takes_chosen_groups_and_defaults_the_rest():
    app = _app()
    copy = copied_settings(app, {"personality", "model"})
    assert (copy.provider, copy.model) == ("anthropic", "claude-sonnet-5-5")
    assert copy.personality == "Friendly" and copy.knowledge_notes == "SKUs start with CC"
    assert copy.temperature == 0.2 and copy.auto_route and copy.router == "llm"
    # Scope and limits weren't chosen: defaults.
    assert copy.in_scope == "" and copy.classifier_enabled and copy.classifier_strictness == "balanced"
    assert copy.messages_per_minute == 10
    assert not copy.is_public


def test_copy_with_nothing_chosen_keeps_only_the_model():
    copy = copied_settings(_app(), set())
    assert (copy.provider, copy.model) == ("anthropic", "claude-sonnet-5-5")
    assert copy.personality == "" and copy.route_tiers == [] and not copy.auto_route
