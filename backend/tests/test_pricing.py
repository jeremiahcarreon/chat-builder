from app.pricing import builtin_price, cost, price_for


def test_builtin_prices_match_by_longest_prefix():
    assert builtin_price("anthropic", "claude-opus-5-5") == (4.0, 20.0)
    assert builtin_price("anthropic", "claude-opus-5") == (5.0, 25.0)
    assert builtin_price("anthropic", "claude-haiku-4-5-20251001") == (1.0, 5.0)
    assert builtin_price("ollama", "anything") == (0.0, 0.0)
    assert builtin_price("openai", "gpt-5") is None


def test_custom_prices_override():
    custom = {"openai/gpt-5": (1.25, 10.0), "anthropic/claude-opus-5-5": (3.0, 15.0)}
    assert price_for("openai", "gpt-5", custom) == (1.25, 10.0)
    assert price_for("anthropic", "claude-opus-5-5", custom) == (3.0, 15.0)


def test_cost():
    assert cost(1_000_000, 500_000, (4.0, 20.0)) == 14.0
    assert cost(10, 10, None) is None
