"""LLM providers the app supports, model listing, and API key checks.

Key checks list the provider's models rather than generating text, so testing
a key costs nothing.
"""

from dataclasses import dataclass

import anthropic
import httpx
import openai
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import decrypt
from app.models import ProviderKey


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    key_hint: str
    needs_key: bool = True
    # Suggested defaults shown in the app editor.
    default_model: str = ""
    default_classifier_model: str = ""


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in [
        Provider(
            "anthropic",
            "Anthropic (Claude)",
            "sk-ant-...",
            default_model="claude-opus-5-5",
            default_classifier_model="claude-haiku-4-5",
        ),
        Provider(
            "openai", "OpenAI", "sk-...", default_model="gpt-5", default_classifier_model="gpt-5-mini"
        ),
        Provider(
            "gemini",
            "Google Gemini",
            "AIza...",
            default_model="gemini-2.5-pro",
            default_classifier_model="gemini-2.5-flash",
        ),
        Provider(
            "ollama",
            "Local models (Ollama)",
            "",
            needs_key=False,
            default_model="llama3.2",
            default_classifier_model="llama3.2",
        ),
    ]
}

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class ProviderError(Exception):
    """A user-facing problem talking to a provider."""


def key_context(provider: str) -> str:
    return f"provider_key:{provider}"


def get_api_key(db: Session, user_id, provider: str) -> str:
    """Returns the decrypted key for a provider, or raises ProviderError."""
    if not PROVIDERS[provider].needs_key:
        return "ollama"
    row = db.scalar(
        select(ProviderKey).where(ProviderKey.user_id == user_id, ProviderKey.provider == provider)
    )
    if row is None:
        raise ProviderError(f"No API key saved for {PROVIDERS[provider].label}. Add one in Settings.")
    return decrypt(row.encrypted_key, key_context(provider))


def openai_compatible_client(provider: str, api_key: str) -> openai.AsyncOpenAI:
    match provider:
        case "openai":
            return openai.AsyncOpenAI(api_key=api_key)
        case "gemini":
            return openai.AsyncOpenAI(api_key=api_key, base_url=GEMINI_OPENAI_BASE_URL)
        case "ollama":
            return openai.AsyncOpenAI(api_key=api_key, base_url=f"{get_settings().ollama_url}/v1")
    raise ValueError(f"{provider} is not an OpenAI-compatible provider")


async def list_models(provider: str, api_key: str) -> list[str]:
    """Model IDs available to this key. Raises ProviderError with a readable message."""
    try:
        if provider == "anthropic":
            client = anthropic.AsyncAnthropic(api_key=api_key, timeout=15, max_retries=1)
            return [m.id async for m in client.models.list()]
        if provider == "ollama":
            async with httpx.AsyncClient(timeout=10) as http:
                response = await http.get(f"{get_settings().ollama_url}/api/tags")
                response.raise_for_status()
            return sorted(m["name"] for m in response.json().get("models", []))
        client = openai_compatible_client(provider, api_key)
        models = [m.id async for m in client.with_options(timeout=15, max_retries=1).models.list()]
        # Gemini's compatible endpoint prefixes IDs with "models/".
        return sorted(m.removeprefix("models/") for m in models)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise ProviderError("The provider rejected this key.") from None
    except (openai.AuthenticationError, openai.PermissionDeniedError):
        raise ProviderError("The provider rejected this key.") from None
    except openai.BadRequestError as exc:
        # Gemini reports an invalid key as a 400.
        raise ProviderError("The provider rejected this key.") from exc
    except (anthropic.APIConnectionError, openai.APIConnectionError, httpx.HTTPError):
        raise ProviderError(f"Could not reach {PROVIDERS[provider].label}.") from None
    except (anthropic.APIStatusError, openai.APIStatusError) as exc:
        raise ProviderError(f"Unexpected response from provider (HTTP {exc.status_code}).") from None


async def test_api_key(provider: str, api_key: str) -> tuple[bool, str | None]:
    """Returns (ok, error message)."""
    try:
        await list_models(provider, api_key)
    except ProviderError as exc:
        return False, str(exc)
    return True, None
