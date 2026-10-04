from sqlalchemy.orm import Session

from app.llm.anthropic_client import AnthropicClient
from app.llm.base import LLMClient
from app.llm.openai_client import OpenAICompatibleClient
from app.providers import get_api_key


def client_for(db: Session, user_id, provider: str) -> LLMClient:
    """Builds a client using the owner's saved key. Raises ProviderError if no key is saved."""
    api_key = get_api_key(db, user_id, provider)
    if provider == "anthropic":
        return AnthropicClient(api_key)
    return OpenAICompatibleClient(provider, api_key)
