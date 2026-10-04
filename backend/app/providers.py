"""LLM providers the app supports, and a cheap way to check an API key works.

Key checks list the provider's models rather than generating text, so testing
a key costs nothing.
"""

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    key_hint: str


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in [
        Provider("anthropic", "Anthropic (Claude)", "sk-ant-..."),
        Provider("openai", "OpenAI", "sk-..."),
        Provider("gemini", "Google Gemini", "AIza..."),
    ]
}


def _request_for(provider: str, api_key: str) -> tuple[str, dict[str, str]]:
    match provider:
        case "anthropic":
            return "https://api.anthropic.com/v1/models", {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
        case "openai":
            return "https://api.openai.com/v1/models", {"Authorization": f"Bearer {api_key}"}
        case "gemini":
            return "https://generativelanguage.googleapis.com/v1beta/models", {
                "x-goog-api-key": api_key
            }
    raise ValueError(f"Unknown provider: {provider}")


async def test_api_key(provider: str, api_key: str) -> tuple[bool, str | None]:
    """Returns (ok, error message)."""
    url, headers = _request_for(provider, api_key)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return False, f"Could not reach {PROVIDERS[provider].label}: {exc.__class__.__name__}"
    if response.status_code == 200:
        return True, None
    if response.status_code in (400, 401, 403):
        return False, "The provider rejected this key."
    return False, f"Unexpected response from provider (HTTP {response.status_code})."
