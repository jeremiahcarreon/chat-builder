from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.crypto import decrypt, encrypt
from app.deps import CurrentUser, DbSession
from app.models import ProviderKey
from app.providers import PROVIDERS, test_api_key

router = APIRouter(prefix="/api/settings/providers", tags=["settings"])


class ProviderKeyOut(BaseModel):
    provider: str
    label: str
    key_hint: str
    configured: bool
    last4: str | None = None
    last_tested_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_error: str | None = None


class SaveKeyRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=500)


def _context(provider: str) -> str:
    return f"provider_key:{provider}"


def _check_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider")


def _get_key(db: DbSession, user: CurrentUser, provider: str) -> ProviderKey | None:
    return db.scalar(
        select(ProviderKey).where(ProviderKey.user_id == user.id, ProviderKey.provider == provider)
    )


def _out(provider: str, row: ProviderKey | None) -> ProviderKeyOut:
    meta = PROVIDERS[provider]
    out = ProviderKeyOut(
        provider=provider, label=meta.label, key_hint=meta.key_hint, configured=row is not None
    )
    if row is not None:
        out.last4 = row.last4
        out.last_tested_at = row.last_tested_at
        out.last_test_ok = row.last_test_ok
        out.last_test_error = row.last_test_error
    return out


@router.get("")
def list_keys(db: DbSession, user: CurrentUser) -> list[ProviderKeyOut]:
    rows = {
        row.provider: row
        for row in db.scalars(select(ProviderKey).where(ProviderKey.user_id == user.id))
    }
    return [_out(provider, rows.get(provider)) for provider in PROVIDERS]


@router.put("/{provider}")
async def save_key(
    provider: str, body: SaveKeyRequest, db: DbSession, user: CurrentUser
) -> ProviderKeyOut:
    _check_provider(provider)
    api_key = body.api_key.strip()
    ok, error = await test_api_key(provider, api_key)

    row = _get_key(db, user, provider)
    if row is None:
        row = ProviderKey(user_id=user.id, provider=provider)
        db.add(row)
    row.encrypted_key = encrypt(api_key, _context(provider))
    row.last4 = api_key[-4:]
    row.last_tested_at = datetime.now(UTC)
    row.last_test_ok = ok
    row.last_test_error = error
    db.commit()
    return _out(provider, row)


@router.post("/{provider}/test")
async def test_key(provider: str, db: DbSession, user: CurrentUser) -> ProviderKeyOut:
    _check_provider(provider)
    row = _get_key(db, user, provider)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No key saved for this provider")
    ok, error = await test_api_key(provider, decrypt(row.encrypted_key, _context(provider)))
    row.last_tested_at = datetime.now(UTC)
    row.last_test_ok = ok
    row.last_test_error = error
    db.commit()
    return _out(provider, row)


@router.delete("/{provider}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(provider: str, db: DbSession, user: CurrentUser) -> None:
    _check_provider(provider)
    row = _get_key(db, user, provider)
    if row is not None:
        db.delete(row)
        db.commit()
