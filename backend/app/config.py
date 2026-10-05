import base64
import binascii
from functools import lru_cache

from pydantic import EmailStr, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    master_key: str = Field(description="Base64-encoded 32-byte key for encrypting secrets")

    admin_email: EmailStr
    admin_password: str = Field(min_length=12)

    ollama_url: str = "http://ollama:11434"
    embedding_model: str = "embeddinggemma"

    session_days: int = 7
    # The address people reach Chat Builder at, e.g. https://chat.example.com.
    # Used for invite links and embed snippets; defaults to the request's address.
    public_url: str = ""
    # Set to true once the app is served over HTTPS.
    cookie_secure: bool = False

    static_dir: str = "/app/static"
    upload_dir: str = "/data/uploads"
    max_upload_mb: int = 50

    @field_validator("master_key")
    @classmethod
    def _check_master_key(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raw = b""
        if len(raw) != 32:
            raise ValueError(
                "MASTER_KEY must be 32 random bytes, base64-encoded "
                "(generate one with: openssl rand -base64 32)"
            )
        return value

    @property
    def master_key_bytes(self) -> bytes:
        return base64.b64decode(self.master_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
