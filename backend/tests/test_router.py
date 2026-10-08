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
    assert request_params("ollama", "qwen3:14b", Sampling(effort="none")) == {"reasoning_effort": "none"}
    assert request_params("ollama", "qwen2.5:3b", Sampling()) == {}
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


def test_better_answer_goes_one_step_above_the_answer():
    app = _app(auto_route=True)
    route = router.better_route(app, floor=0, last_step=1)
    assert route.step.label == "anthropic/claude-sonnet-5-5 (high effort)"
    assert (route.floor, route.escalated) == (2, True)
    # Not routed before (routing turned on later): one step above the floor.
    assert router.better_route(app, floor=0, last_step=None).step_index == 1
    # Already the top: nothing better to offer.
    assert router.better_route(app, floor=0, last_step=3) is None


def test_better_answer_is_offered_below_the_top_only():
    app = _app(auto_route=True)
    assert router.can_improve(app, 2)
    assert not router.can_improve(app, 3)
    assert not router.can_improve(app, None)
    assert not router.can_improve(_app(auto_route=False), 0)


def test_calibration_and_baseline_use_each_tiers_first_step():
    app = _app(auto_route=True)
    assert router.tier_route(app, 1).step.label == "anthropic/claude-sonnet-5-5 (low effort)"
    assert router.tier_route(app, 5) is None
    assert router.baseline_label(app) == "anthropic/claude-opus-5-5"


def test_calibration_suggestions():
    from types import SimpleNamespace

    from app.routers.quality import CalibrationCase, suggest

    runs = [SimpleNamespace(tier=i, model_label=name) for i, name in enumerate(["Haiku", "Sonnet", "Opus"])]
    rows = [
        CalibrationCase(question="a", passed=[True, True, True], cheapest=0),
        CalibrationCase(question="b", passed=[True, True, True], cheapest=0),
        CalibrationCase(question="c", passed=[False, False, True], cheapest=2),
        CalibrationCase(question="d", passed=[False, False, False], cheapest=None),
    ]
    tips = suggest(runs, rows)
    assert tips[0] == "Haiku answered 2 of 4 questions correctly on its own."
    assert "No question needed Sonnet; consider removing it from the ladder." in tips
    assert "Opus was first to get 1 more right." in tips
    assert tips[-1].startswith("1 question failed on every model.")


def test_local_thinking_models_can_switch_thinking_off():
    from app.llm.sampling import effort_levels

    assert effort_levels("ollama", "qwen3:14b") == ["none", "high"]
    assert effort_levels("ollama", "gpt-oss:20b") == ["low", "medium", "high"]
    assert effort_levels("ollama", "qwen2.5:7b-instruct") == []


def test_sql_written_instead_of_run_is_picked_up():
    from types import SimpleNamespace

    from app.connectors.query import SQL_TOOL
    from app.llm.base import StepResult
    from app.llm.engine import written_sql_call

    sid = "8420ac60-10b3-48f5-890c-88c0b64813d0"
    ctx = SimpleNamespace(queryable={sid: ("mssql", "Catalog")})
    fenced = StepResult("Let's run:\n```sql\nSELECT * FROM dbo.Shipping WHERE PartNo = 'EC797';\n```\nOne moment.")
    assert written_sql_call(ctx, [SQL_TOOL], fenced).arguments == {"source_id": sid, "sql": "SELECT * FROM dbo.Shipping WHERE PartNo = 'EC797'"}
    inline = StepResult("Run this query: `SELECT PartNo FROM dbo.FitmentCondensed WHERE Make = 'BMW'` to see.")
    assert written_sql_call(ctx, [SQL_TOOL], inline).arguments["sql"].startswith("SELECT PartNo")
    pasted = StepResult('{"name": "sql_query", "arguments": {"source_id": "%s", "sql": "SELECT 1"}} </tool_call>' % sid)
    assert written_sql_call(ctx, [SQL_TOOL], pasted).arguments == {"source_id": sid, "sql": "SELECT 1"}
    # Real tool calls, no tools offered, or no SQL: nothing to do.
    assert written_sql_call(ctx, [], fenced) is None
    assert written_sql_call(ctx, [SQL_TOOL], StepResult("The CC797 has a 2 inch core.")) is None
