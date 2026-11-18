"""The review gate: which answers it holds, how intents earn "auto", and the one rewrite."""

import asyncio
import uuid
from types import SimpleNamespace

from app import gate
from app.connectors.mcp import tool_risk
from app.llm import engine
from app.llm.base import StepResult, ToolCall, ToolResult

SETTINGS = gate.GateSettings(enabled=True, intents=["fitment", "Order  Status"], min_samples=5, window=10)


def _record(tier="auto", pinned=False, reviewed=10, sent=10, intent="fitment"):
    return gate.IntentRecord(intent, tier, pinned, reviewed, sent)


def _decide(record=None, tool_calls=None, check=None, roll=0.99):
    meta = {"tool_calls": tool_calls or []}
    return gate.decide(SETTINGS, record or _record(), meta, check or gate.Check("fitment", []), False, roll=roll)


def test_settings_normalise_intent_names():
    assert SETTINGS.intents == ["fitment", "order status"]
    assert gate.GateSettings(pinned={"Fitment ": "auto", "x": "sometimes"}).pinned == {"fitment": "auto"}


def test_a_grounded_answer_from_an_earned_intent_goes_out_on_its_own():
    verdict = _decide(tool_calls=[{"name": "erp__find_fitment", "risk": "read"}])
    assert verdict["status"] == gate.AUTO
    assert verdict["reasons"] == [] and verdict["grounded"] is True and verdict["risk"] == "read"


def test_an_irreversible_tool_holds_but_a_reversible_one_does_not():
    held = _decide(tool_calls=[{"name": "erp__propose_rma", "risk": "irreversible"}])
    assert held["status"] == gate.HOLD and held["reasons"] == ["Changed data with erp__propose_rma"]
    logged = _decide(tool_calls=[{"name": "erp__add_note", "risk": "reversible"}])
    assert logged["status"] == gate.AUTO and logged["risk"] == "reversible"


def test_a_tool_that_failed_changed_nothing_but_still_holds():
    verdict = _decide(tool_calls=[{"name": "erp__propose_rma", "risk": "irreversible", "error": True}])
    assert verdict["reasons"] == ["A lookup failed"]


def test_unsupported_claims_and_failed_checks_hold():
    unsupported = _decide(check=gate.Check("fitment", ["CC161 fits the 2010 Murano"]))
    assert unsupported["status"] == gate.HOLD and unsupported["grounded"] is False
    assert "CC161 fits the 2010 Murano" in unsupported["reasons"][0]
    failed = _decide(check=gate.Check("general", [], ran=False))
    assert failed["grounded"] is None and failed["status"] == gate.HOLD


def test_intents_need_approval_until_earned_and_pins_win():
    assert gate.earned_tier(SETTINGS, ["sent"] * 4) == "approval", "too few reviewed"
    assert gate.earned_tier(SETTINGS, ["sent"] * 9 + ["edited"]) == "approval", "90% is under 95%"
    assert gate.earned_tier(SETTINGS, ["sent"] * 10) == gate.AUTO
    waiting = _decide(record=_record(tier="approval", reviewed=3, sent=3))
    assert waiting["status"] == gate.HOLD and "3 of 3 so far" in waiting["reasons"][0]
    pinned = _decide(record=_record(tier="approval", pinned=True))
    assert pinned["reasons"] == ["Fitment answers always need approval"]


def test_auto_intents_still_hold_spot_checks():
    assert _decide(roll=0.05)["reasons"] == ["Spot check"]
    assert _decide(roll=0.5)["status"] == gate.AUTO


def test_only_held_answers_with_a_decision_count():
    assert gate.outcome_of({"api_outcome": {"outcome": "edited"}}) == "edited"
    assert gate.outcome_of({"review": {"status": "sent"}}) == "sent"
    assert gate.outcome_of({"review": {"status": "held"}}) is None


def test_the_check_is_parsed_leniently():
    intents = ["fitment", "order status"]
    assert gate.parse_check('Sure: {"intent": "Fitment", "unsupported": []}', intents) == ("fitment", [])
    assert gate.parse_check('{"intent": "order", "unsupported": ["x"]}', intents) == ("order status", ["x"])
    assert gate.parse_check('{"intent": "billing", "unsupported": []}', intents)[0] == gate.GENERAL


def test_tool_risk_follows_mcp_hints_unless_the_admin_overrides():
    assert tool_risk({"name": "find", "read_only": True}) == "read"
    assert tool_risk({"name": "note", "read_only": False, "destructive": False}) == "reversible"
    assert tool_risk({"name": "rma", "read_only": False}) == "irreversible", "MCP's default is destructive"
    assert tool_risk({"name": "rma", "read_only": False}, {"rma": "reversible"}) == "reversible"


def test_api_calls_and_reviewed_visitors_go_through_the_gate():
    app = SimpleNamespace(gate={"enabled": True}, review_answers=False)

    def req(**kwargs):
        base = {"app_id": uuid.uuid4(), "message": "q", "conversation_id": None, "user_id": None, "visitor_id": "v1"}
        return engine.ChatRequest(**{**base, **kwargs})

    assert gate.applies(app, req(visitor_id="api:key"), None), "API calls, even without a thread ID"
    assert not gate.applies(app, req(), None), "visitors only when the app reviews their answers"
    assert gate.applies(SimpleNamespace(gate={"enabled": True}, review_answers=True), req(), None)
    assert not gate.applies(app, req(user_id=uuid.uuid4()), None), "signed-in staff get answers live"
    assert not gate.applies(SimpleNamespace(gate={}, review_answers=True), req(visitor_id="api:key"), None)


class RewriteSession:
    """Answers with a claim it can't back up, then rewrites when told."""

    accepts_user_messages = True

    def __init__(self):
        self.round = 0
        self.told = None

    async def step(self, tools, on_text, on_reset):
        self.round += 1
        if self.round == 1:
            return StepResult(text="", tool_calls=[ToolCall("1", "erp__find_fitment", {})], stop_reason="tool_use")
        text = "It fits, and ships today." if self.round == 2 else "It fits."
        await on_text(text)
        return StepResult(text=text)

    def retry_empty(self):
        return False

    def add_tool_results(self, results):
        pass

    def add_user_message(self, text):
        self.told = text


def test_an_unsupported_claim_gets_one_rewrite(monkeypatch):
    tool = SimpleNamespace(risk="read")
    ctx = SimpleNamespace(source_names=[], has_documents=False, mcp_tools={"erp__find_fitment": tool}, call_context={},
                          excerpts=[("Policy", "Returns within 30 days")], table_descriptions=[])

    async def gather(*args):
        return ctx

    async def run_tool(ctx, call):
        return ToolResult(call, '{"fits": true}')

    monkeypatch.setattr(engine.context_module, "gather", gather)
    monkeypatch.setattr(engine.context_module, "tools_for", lambda ctx: [])
    monkeypatch.setattr(engine.context_module, "run_tool", run_tool)
    monkeypatch.setattr(engine.context_module, "describe_call", lambda ctx, call: call.name)
    monkeypatch.setattr(engine, "build_system_prompt", lambda *a, **k: "")
    monkeypatch.setattr(engine, "build_user_turn", lambda *a, **k: "")
    monkeypatch.setattr(engine, "_usage_event", lambda *a, **k: None)
    session = RewriteSession()
    client = SimpleNamespace(session=lambda **kwargs: session)
    app = SimpleNamespace(id=uuid.uuid4(), max_answer_tokens=100, temperature=None, seed=None)
    checked = []

    async def gate_check(gen):
        checked.append(gen.text)
        assert gen.results == [("erp__find_fitment", '{"fits": true}')]
        return gate.Check("fitment", ["ships today"] if "ships" in gen.text else [])

    async def emit(event):
        pass

    gen = engine.Generation(meta={})
    asyncio.run(engine.generate(app, client, "x", "m", [], "q", gen, emit, final_only=True, gate_check=gate_check))

    assert gen.text == "It fits."
    assert gen.rewritten and gen.check.unsupported == []
    assert checked == ["It fits, and ships today.", "It fits."]
    assert "ships today" in session.told
    assert gen.meta["tool_calls"][0]["risk"] == "read"
    assert gen.excerpts == [("Policy", "Returns within 30 days")]
