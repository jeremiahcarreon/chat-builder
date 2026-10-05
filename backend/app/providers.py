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
    # False for classifier-only providers (TypeSafe's Jev answers typed questions, not chat).
    chat: bool = True
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
            # Local models are whatever has been pulled into Ollama, so there is
            # no safe default; the app's own model is used for the classifier.
        ),
        Provider(
            "typesafe",
            "TypeSafe (Jev)",
            "sk-...",
            chat=False,
            default_classifier_model="jev-latest",
        ),
    ]
}

# TypeSafe AI's System One model: a fast, inexpensive classifier. When the owner
# has a key, it screens messages for every app without its own classifier choice.
JEV_PROVIDER = "typesafe"
CHAT_PROVIDERS = tuple(p.id for p in PROVIDERS.values() if p.chat)

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class ProviderError(Exception):
    """A user-facing problem talking to a provider."""


def key_context(provider: str) -> str:
    return f"provider_key:{provider}"


def get_api_key(db: Session, user_id, provider: str) -> str:
    """Returns the decrypted key for a provider, or raises ProviderError."""
    if not PROVIDERS[provider].needs_key:
        return "ollama"
    # Keys are shared by the whole workspace; user_id is kept for callers' clarity.
    row = workspace_key(db, provider)
    if row is None:
        raise ProviderError(f"No API key saved for {PROVIDERS[provider].label}. Add one in Settings.")
    return decrypt(row.encrypted_key, key_context(provider))


def has_key(db: Session, user_id, provider: str) -> bool:
    return workspace_key(db, provider) is not None


def workspace_key(db: Session, provider: str) -> ProviderKey | None:
    """The workspace's saved key for a provider (the most recently saved, if several)."""
    return db.scalar(
        select(ProviderKey).where(ProviderKey.provider == provider).order_by(ProviderKey.updated_at.desc()).limit(1)
    )


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
        if provider == JEV_PROVIDER:
            from app.guardrails.jev import JevClient
            from app.llm.base import LLMError

            client = JevClient(api_key)
            try:
                return await client.list_models()
            except LLMError as exc:
                raise ProviderError(str(exc)) from None
            finally:
                await client.aclose()
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
