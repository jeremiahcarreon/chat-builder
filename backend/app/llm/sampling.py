"""Temperature, seed and effort, sent only to models that accept them.

Several current models reject sampling parameters outright (the request fails
with a 400), so support is decided per provider and model.

Effort is how much work a reasoning model puts into an answer. Automatic model
choice starts low and raises it when an answer falls short. Claude takes it as
output_config.effort; OpenAI and Gemini as reasoning_effort.
"""

import re
from dataclasses import dataclass

# Claude models that still accept temperature. Newer Claude models (Opus 4.7 and
# later, Fable, Sonnet 5 and later) reject it or allow only the default.
ANTHROPIC_TEMPERATURE = re.compile(r"^claude-(3|haiku-4|sonnet-4|opus-4-[0-6]|opus-4$|sonnet-4$|opus-4-1)")
# OpenAI reasoning models only accept the default temperature.
OPENAI_REASONING = re.compile(r"^(o\d|gpt-5|gpt-oss)")


# Effort levels, lowest first. "none" turns thinking off (local thinking models).
EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
# Claude models known to accept effort (the Models API reports it for new ones).
ANTHROPIC_EFFORT = re.compile(r"^claude-(opus-4-[5-9]|sonnet-4-[6-9]|(opus|sonnet|fable)-[5-9])")
GEMINI_EFFORT = re.compile(r"^gemini-([3-9]|2\.5)")
# Local models that think before answering; Ollama turns thinking off with "none".
OLLAMA_THINKING = re.compile(r"^(qwen3|deepseek-r1|magistral|phi4-reasoning|gpt-oss)")


@dataclass(frozen=True)
class Sampling:
    temperature: float | None = None
    seed: int | None = None
    effort: str | None = None


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


def effort_levels(provider: str, model: str) -> list[str]:
    """Effort levels a model is known to accept, lowest first (empty if none)."""
    model = model.strip().lower()
    if provider == "anthropic" and ANTHROPIC_EFFORT.match(model):
        return ["low", "medium", "high", "max"]
    if provider == "openai" and OPENAI_REASONING.match(model):
        return ["low", "medium", "high"]
    if provider == "gemini" and GEMINI_EFFORT.match(model):
        return ["low", "medium", "high"]
    if provider == "ollama" and OLLAMA_THINKING.match(model):
        # gpt-oss always thinks some; the others can switch it off.
        return ["low", "medium", "high"] if model.startswith("gpt-oss") else ["none", "high"]
    if provider == "scripted":
        return ["low", "high"]
    return []


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
    if sampling.effort:
        # Sent as chosen: tiers are checked against the provider's model list when saved.
        if provider == "anthropic":
            params["output_config"] = {"effort": sampling.effort}
        else:
            params["reasoning_effort"] = sampling.effort
    return params


def without_effort(params: dict) -> dict:
    """The same parameters with effort removed (after a model rejected it)."""
    return {k: v for k, v in params.items() if k not in ("output_config", "reasoning_effort")}


# Screening should be as repeatable as possible.
CLASSIFIER_SAMPLING = Sampling(temperature=0.0, seed=7)
