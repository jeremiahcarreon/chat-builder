from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import distinct, select

from app.deps import AdminUser, DbSession
from app.models import ChatApp, UsageEvent
from app.pricing import builtin_price, key, load_custom, save_custom
from app.providers import PROVIDERS

router = APIRouter(prefix="/api/settings/prices", tags=["settings"])


class PriceOut(BaseModel):
    provider: str
    model: str
    input: float | None
    output: float | None
    # builtin | custom | missing
    source: str


class PriceIn(BaseModel):
    provider: str
    model: str = Field(min_length=1, max_length=200)
    input: float = Field(ge=0, le=10_000)
    output: float = Field(ge=0, le=10_000)


@router.get("")
def list_prices(db: DbSession, user: AdminUser) -> list[PriceOut]:
    """Prices for every model this account has used, plus any set by hand."""
    custom = load_custom(db, user.id)
    used = db.execute(
        select(distinct(UsageEvent.provider), UsageEvent.model)
        .join(ChatApp, ChatApp.id == UsageEvent.app_id)
        
    ).all()
    pairs = {(p, m) for p, m in used} | {tuple(k.split("/", 1)) for k in custom}
    out = []
    for provider, model in sorted(pairs):
        if (k := key(provider, model)) in custom:
            out.append(PriceOut(provider=provider, model=model, input=custom[k][0], output=custom[k][1], source="custom"))
        elif (builtin := builtin_price(provider, model)) is not None:
            out.append(PriceOut(provider=provider, model=model, input=builtin[0], output=builtin[1], source="builtin"))
        else:
            out.append(PriceOut(provider=provider, model=model, input=None, output=None, source="missing"))
    return out


@router.put("")
def set_price(body: PriceIn, db: DbSession, user: AdminUser) -> list[PriceOut]:
    if body.provider not in PROVIDERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown provider")
    custom = load_custom(db, user.id)
    custom[key(body.provider, body.model.strip())] = (body.input, body.output)
    save_custom(db, user.id, custom)
    return list_prices(db, user)


@router.delete("")
def reset_price(provider: str, model: str, db: DbSession, user: AdminUser) -> list[PriceOut]:
    custom = load_custom(db, user.id)
    custom.pop(key(provider, model), None)
    save_custom(db, user.id, custom)
    return list_prices(db, user)
