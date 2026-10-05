"""Model prices for cost estimates, in US dollars per million tokens.

Claude prices come from Anthropic's published list; local models are free.
Owners can add or override prices for any model in Settings (OpenAI and
Gemini prices change often, so none are built in).
"""

import uuid

from sqlalchemy.orm import Session

from app.models import UserSetting

PRICES_KEY = "model_prices"

# (input, output) per million tokens. Matched by prefix, so dated model IDs
# (e.g. claude-haiku-4-5-20251001) use their family's price.
BUILTIN: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-fable-5-1"): (10.0, 50.0),
    ("anthropic", "claude-fable-5"): (10.0, 50.0),
    ("anthropic", "claude-opus-5-5"): (4.0, 20.0),
    ("anthropic", "claude-opus-5"): (5.0, 25.0),
    ("anthropic", "claude-opus-4-8"): (5.0, 25.0),
    ("anthropic", "claude-opus-4-7"): (5.0, 25.0),
    ("anthropic", "claude-opus-4-6"): (5.0, 25.0),
    ("anthropic", "claude-sonnet-5-5"): (2.0, 10.0),
    ("anthropic", "claude-sonnet-5"): (2.0, 10.0),
    ("anthropic", "claude-sonnet-4-6"): (3.0, 15.0),
    ("anthropic", "claude-haiku-4-5"): (1.0, 5.0),
}


def load_custom(db: Session, user_id: uuid.UUID) -> dict[str, tuple[float, float]]:
    row = db.get(UserSetting, (user_id, PRICES_KEY))
    if row is None:
        return {}
    return {key: (float(v["input"]), float(v["output"])) for key, v in row.value.items()}


def save_custom(db: Session, user_id: uuid.UUID, prices: dict[str, tuple[float, float]]) -> None:
    row = db.get(UserSetting, (user_id, PRICES_KEY))
    if row is None:
        row = UserSetting(user_id=user_id, key=PRICES_KEY, value={})
        db.add(row)
    row.value = {key: {"input": i, "output": o} for key, (i, o) in prices.items()}
    db.commit()


def key(provider: str, model: str) -> str:
    return f"{provider}/{model}"


def builtin_price(provider: str, model: str) -> tuple[float, float] | None:
    if provider == "ollama":
        return (0.0, 0.0)
    # Longest matching prefix wins (claude-opus-5-5 before claude-opus-5).
    matches = [(m, price) for (p, m), price in BUILTIN.items() if p == provider and model.startswith(m)]
    return max(matches, key=lambda item: len(item[0]))[1] if matches else None


def price_for(provider: str, model: str, custom: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    return custom.get(key(provider, model)) or builtin_price(provider, model)


def cost(input_tokens: int, output_tokens: int, price: tuple[float, float] | None) -> float | None:
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000
