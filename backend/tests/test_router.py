"""Automatic model choice: the ladder, escalation, and the router's questions."""

import json
import uuid

import httpx2

from app.guardrails.jev import JevClient
from app.llm import router
from app.llm.sampling import Sampling, request_params, without_effort
from app.models import ChatApp

CLAUDE = [
    {"provider": "anthropic", "model": "claude-haiku-4-5", "efforts": []},
    {"provider": "anthropic", "model": "claude-sonnet-5-5", "efforts": ["low", "high"]},
    {"provider": "anthropic", "model": "claude-opus-5-5", "efforts": ["medium"]},
]


def _app(**overrides) -> ChatApp:
    values = dict(
        id=uuid.uuid4(), owner_id=uuid.uuid4(), name="Parts Desk", description="Answers parts questions",
        provider="anthropic", model="claude-sonnet-5-5", in_scope="parts", route_tiers=CLAUDE, router="jev",
    )
    values.update(overrides)
    return ChatApp(**values)


def test_ladder_flattens_tiers_cheapest_first():
    steps, starts = router.ladder(CLAUDE)
    assert [s.label for s in steps] == [
        "anthropic/claude-haiku-4-5",
        "anthropic/claude-sonnet-5-5 (low effort)",
        "anthropic/claude-sonnet-5-5 (high effort)",
        "anthropic/claude-opus-5-5 (medium effort)",
    ]
    assert starts == [0, 1, 3]


def test_a_tier_starts_at_its_lowest_effort():
    steps, starts = router.ladder(CLAUDE)
    assert router.decide(steps, starts, tier=1, unsatisfied=False, floor=0, last_step=None) == (1, 0, False)


def test_unsatisfied_moves_up_one_step_from_the_last_answer():
    steps, starts = router.ladder(CLAUDE)
    # Sonnet at low effort answered; the person says it missed: Sonnet at high effort.
    assert router.decide(steps, starts, tier=1, unsatisfied=True, floor=0, last_step=1) == (2, 2, True)
    # Again: up to Opus.
    assert router.decide(steps, starts, tier=1, unsatisfied=True, floor=2, last_step=2) == (3, 3, True)
    # Already at the top: stays there.
    assert router.decide(steps, starts, tier=0, unsatisfied=True, floor=3, last_step=3) == (3, 3, False)


def test_the_floor_keeps_later_easy_questions_at_the_raised_level():
    steps, starts = router.ladder(CLAUDE)
    assert router.decide(steps, starts, tier=0, unsatisfied=False, floor=2, last_step=2) == (2, 2, False)
    # A harder question still goes higher than the floor.
    assert router.decide(steps, starts, tier=2, unsatisfied=False, floor=2, last_step=2) == (3, 2, False)


def test_out_of_range_values_are_clamped_after_the_tiers_change():
    steps, starts = router.ladder(CLAUDE[:2])
    assert router.decide(steps, starts, tier=5, unsatisfied=True, floor=9, last_step=9) == (2, 2, False)


def test_tier_descriptions_default_by_count_and_can_be_written():
    assert router.descriptions(CLAUDE)[0].startswith("Simple")
    assert router.descriptions(CLAUDE)[2].startswith("Complex")
    custom = [dict(CLAUDE[0], use_for="Stock and price lookups"), CLAUDE[1]]
    assert router.descriptions(custom) == ["Stock and price lookups", router.DIFFICULTY[2][1]]


def test_llm_reply_parsing():
    assert router.parse_llm('{"level": 3, "unsatisfied": true}', 3) == (2, True)
    assert router.parse_llm('Sure: {"level": "9"}', 3) == (2, False)
    assert router.parse_llm('{"level": 0, "unsatisfied": "false"}', 3) == (0, False)


def test_effort_goes_to_each_provider_in_its_own_form():
    assert request_params("anthropic", "claude-sonnet-5-5", Sampling(effort="low")) == {"output_config": {"effort": "low"}}
    assert request_params("openai", "gpt-5", Sampling(effort="high")) == {"reasoning_effort": "high"}
    assert request_params("gemini", "gemini-2.5-pro", Sampling(effort="low")) == {"reasoning_effort": "low"}
    assert request_params("ollama", "qwen2.5:3b", Sampling(effort="low")) == {}
    assert without_effort({"reasoning_effort": "low", "seed": 1}) == {"seed": 1}


async def test_jev_answers_difficulty_and_satisfaction_in_one_call():
    captured = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        captured.append(body)
        answers = {
            "difficulty": {"type": "choice", "choice": "level2", "confidence": 0.7,
                           "probabilities": {"level1": 0.2, "level2": 0.7, "level3": 0.1}},
        }
        if "unsatisfied" in body["questions"]:
            answers["unsatisfied"] = {"type": "noul", "noul": 0.83}
        return httpx2.Response(200, json={"model": "jev-1", "usage": {"input_tokens": 300, "output_tokens": 0}, "answers": answers})

    client = JevClient("sk-test", transport=httpx2.MockTransport(handler))
    app = _app()
    levels = router.descriptions(CLAUDE)
    history = [("user", "Price of FS161?"), ("assistant", "It's $41.25.")]
    tier, unsatisfied, details, usage = await router.ask_jev(client, "jev-latest", app, levels, history, "That's wrong, I meant the shroud")
    assert (tier, unsatisfied) == (1, 0.83)
    assert details["difficulty"]["level2"] == 0.7
    assert usage.input_tokens == 300
    questions = captured[0]["questions"]
    assert questions["difficulty"]["criteria"]["level1"].startswith("Simple")
    assert captured[0]["state"]["recent_conversation"][-1] == {"role": "assistant", "text": "It's $41.25."}

    # A first message has no earlier answer to be unhappy with.
    tier, unsatisfied, _, _ = await router.ask_jev(client, "jev-latest", app, levels, [], "Price of FS161?")
    assert unsatisfied is None
    assert "unsatisfied" not in captured[1]["questions"]
