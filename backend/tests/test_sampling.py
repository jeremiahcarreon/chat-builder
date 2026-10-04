import pytest

from app.llm.sampling import Sampling, request_params, support


@pytest.mark.parametrize(
    "provider,model,temperature,seed",
    [
        ("anthropic", "claude-opus-5-5", False, False),
        ("anthropic", "claude-sonnet-5-5", False, False),
        ("anthropic", "claude-opus-4-7", False, False),
        ("anthropic", "claude-fable-5-1", False, False),
        ("anthropic", "claude-haiku-4-5", True, False),
        ("anthropic", "claude-sonnet-4-6", True, False),
        ("anthropic", "claude-opus-4-6", True, False),
        ("openai", "gpt-5", False, True),
        ("openai", "o3-mini", False, True),
        ("openai", "gpt-4.1", True, True),
        ("gemini", "gemini-2.5-pro", True, True),
        ("ollama", "qwen2.5:3b-instruct", True, True),
    ],
)
def test_support(provider, model, temperature, seed):
    result = support(provider, model)
    assert (result.temperature, result.seed) == (temperature, seed)


def test_unsupported_parameters_are_not_sent():
    sampling = Sampling(temperature=0.3, seed=42)
    assert request_params("anthropic", "claude-opus-5-5", sampling) == {}
    assert request_params("anthropic", "claude-haiku-4-5", sampling) == {"temperature": 0.3}
    assert request_params("openai", "gpt-5", sampling) == {"seed": 42}
    assert request_params("ollama", "llama3.2", sampling) == {"temperature": 0.3, "seed": 42}


def test_defaults_send_nothing():
    assert request_params("ollama", "llama3.2", None) == {}
    assert request_params("ollama", "llama3.2", Sampling()) == {}


def test_temperature_is_clamped():
    assert request_params("gemini", "gemini-2.5-pro", Sampling(temperature=1.7))["temperature"] == 1.0
