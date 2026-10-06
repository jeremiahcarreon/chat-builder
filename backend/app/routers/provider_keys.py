from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.crypto import decrypt, encrypt
from app.deps import AdminUser, DbSession
from app.llm.sampling import support
from app.models import ProviderKey
from app.providers import (
    PROVIDERS,
    ProviderError,
    get_api_key,
    key_context,
    list_models,
    model_efforts,
    test_api_key,
    workspace_key,
)

router = APIRouter(prefix="/api/settings/providers", tags=["settings"])


class ProviderKeyOut(BaseModel):
    provider: str
    label: str
    key_hint: str
    needs_key: bool
    chat: bool
    default_model: str
    default_classifier_model: str
    configured: bool
    last4: str | None = None
    last_tested_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_error: str | None = None


class SaveKeyRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=500)


def _check_provider(provider: str, *, key_required: bool = False) -> None:
    if provider not in PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider")
    if key_required and not PROVIDERS[provider].needs_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This provider doesn't use an API key")


def _get_key(db: DbSession, user: AdminUser, provider: str) -> ProviderKey | None:
    return workspace_key(db, provider)


def _out(provider: str, row: ProviderKey | None) -> ProviderKeyOut:
    meta = PROVIDERS[provider]
    out = ProviderKeyOut(
        provider=provider,
        label=meta.label,
        key_hint=meta.key_hint,
        needs_key=meta.needs_key,
        chat=meta.chat,
        default_model=meta.default_model,
        default_classifier_model=meta.default_classifier_model,
        configured=row is not None or not meta.needs_key,
    )
    if row is not None:
        out.last4 = row.last4
        out.last_tested_at = row.last_tested_at
        out.last_test_ok = row.last_test_ok
        out.last_test_error = row.last_test_error
    return out


@router.get("")
def list_keys(db: DbSession, user: AdminUser) -> list[ProviderKeyOut]:
    rows = {
        row.provider: row
        for row in db.scalars(select(ProviderKey).order_by(ProviderKey.updated_at))
    }
    return [_out(provider, rows.get(provider)) for provider in PROVIDERS]


@router.put("/{provider}")
async def save_key(
    provider: str, body: SaveKeyRequest, db: DbSession, user: AdminUser
) -> ProviderKeyOut:
    _check_provider(provider, key_required=True)
    api_key = body.api_key.strip()
    ok, error = await test_api_key(provider, api_key)

    row = _get_key(db, user, provider)
    if row is None:
        row = ProviderKey(user_id=user.id, provider=provider)
        db.add(row)
    row.encrypted_key = encrypt(api_key, key_context(provider))
    row.last4 = api_key[-4:]
    row.last_tested_at = datetime.now(UTC)
    row.last_test_ok = ok
    row.last_test_error = error
    db.commit()
    return _out(provider, row)


@router.post("/{provider}/test")
async def test_key(provider: str, db: DbSession, user: AdminUser) -> ProviderKeyOut:
    _check_provider(provider)
    if not PROVIDERS[provider].needs_key:
        ok, error = await test_api_key(provider, "")
        out = _out(provider, None)
        out.last_test_ok, out.last_test_error, out.last_tested_at = ok, error, datetime.now(UTC)
        return out
    row = _get_key(db, user, provider)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No key saved for this provider")
    ok, error = await test_api_key(provider, decrypt(row.encrypted_key, key_context(provider)))
    row.last_tested_at = datetime.now(UTC)
    row.last_test_ok = ok
    row.last_test_error = error
    db.commit()
    return _out(provider, row)


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(provider: str, db: DbSession, user: AdminUser) -> None:
    _check_provider(provider, key_required=True)
    row = _get_key(db, user, provider)
    if row is not None:
        db.delete(row)
        db.commit()


@router.get("/{provider}/models")
async def provider_models(provider: str, db: DbSession, user: AdminUser) -> list[str]:
    """Model IDs the saved key can use, for the model picker."""
    _check_provider(provider)
    try:
        return await list_models(provider, get_api_key(db, user.id, provider))
    except ProviderError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None


class SamplingSupportOut(BaseModel):
    temperature: bool
    seed: bool
    note: str
    # Effort levels the model accepts, lowest first (for automatic model choice).
    efforts: list[str]


@router.get("/{provider}/sampling")
async def sampling_support(provider: str, model: str, db: DbSession, _: AdminUser) -> SamplingSupportOut:
    """Whether a model accepts temperature, seed and effort, for the app editor."""
    _check_provider(provider)
    result = support(provider, model)
    try:
        api_key = get_api_key(db, None, provider)
    except ProviderError:
        api_key = None
    efforts = await model_efforts(provider, model, api_key)
    return SamplingSupportOut(temperature=result.temperature, seed=result.seed, note=result.note, efforts=efforts)
