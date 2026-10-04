"""Temperature and seed, sent only to models that accept them.

Several current models reject sampling parameters outright (the request fails
with a 400), so support is decided per provider and model.
"""

import re
from dataclasses import dataclass

# Claude models that still accept temperature. Newer Claude models (Opus 4.7 and
# later, Fable, Sonnet 5 and later) reject it or allow only the default.
ANTHROPIC_TEMPERATURE = re.compile(r"^claude-(3|haiku-4|sonnet-4|opus-4-[0-6]|opus-4$|sonnet-4$|opus-4-1)")
# OpenAI reasoning models only accept the default temperature.
OPENAI_REASONING = re.compile(r"^(o\d|gpt-5|gpt-oss)")


@dataclass(frozen=True)
class Sampling:
    temperature: float | None = None
    seed: int | None = None


@dataclass(frozen=True)
class SamplingSupport:
    temperature: bool
    seed: bool
    note: str = ""


def support(provider: str, model: str) -> SamplingSupport:
    model = model.strip().lower()
    if provider == "anthropic":
        if ANTHROPIC_TEMPERATURE.match(model):
            return SamplingSupport(True, False, "Claude doesn't support a seed.")
        return SamplingSupport(
            False, False, "This Claude model doesn't accept temperature; it always uses its default."
        )
    if provider == "openai":
        if OPENAI_REASONING.match(model):
            return SamplingSupport(
                False, True, "OpenAI reasoning models don't accept temperature; only the seed is used."
            )
        return SamplingSupport(True, True)
    # Gemini and local Ollama models accept both.
    return SamplingSupport(True, True)


def request_params(provider: str, model: str, sampling: Sampling | None) -> dict:
    """The sampling parameters to send for this model (an empty dict means use defaults)."""
    if sampling is None:
        return {}
    allowed = support(provider, model)
    params = {}
    if sampling.temperature is not None and allowed.temperature:
        # Claude's range is 0-1; the others accept up to 2, but the app keeps to 0-1.
        params["temperature"] = max(0.0, min(1.0, float(sampling.temperature)))
    if sampling.seed is not None and allowed.seed:
        params["seed"] = int(sampling.seed)
    return params


# Screening should be as repeatable as possible.
CLASSIFIER_SAMPLING = Sampling(temperature=0.0, seed=7)
