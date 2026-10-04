from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.deps import CurrentUser, DbSession
from app.models import ChatApp, ProviderKey
from app.providers import PROVIDERS
from app.routing import CLOUD_PROVIDERS, LOCAL_PROVIDER, CloudSwitch, load_cloud_switch, save_cloud_switch

router = APIRouter(prefix="/api/settings/cloud-switch", tags=["settings"])


class CloudSwitchIn(BaseModel):
    enabled: bool
    provider: str
    chat_model: str = Field(default="", max_length=200)
    classifier_model: str = Field(default="", max_length=200)


class CloudSwitchOut(CloudSwitchIn):
    # How many apps use local models, so the screen can say what the switch affects.
    local_app_count: int


def _out(db: DbSession, user: CurrentUser, switch: CloudSwitch) -> CloudSwitchOut:
    local = db.scalar(
        select(func.count()).select_from(ChatApp).where(
            ChatApp.owner_id == user.id,
            (ChatApp.provider == LOCAL_PROVIDER) | (ChatApp.classifier_provider == LOCAL_PROVIDER),
        )
    )
    return CloudSwitchOut(
        enabled=switch.enabled,
        provider=switch.provider,
        chat_model=switch.chat_model,
        classifier_model=switch.classifier_model,
        local_app_count=local or 0,
    )


@router.get("")
def get_switch(db: DbSession, user: CurrentUser) -> CloudSwitchOut:
    return _out(db, user, load_cloud_switch(db, user.id))


@router.put("")
def update_switch(body: CloudSwitchIn, db: DbSession, user: CurrentUser) -> CloudSwitchOut:
    if body.provider not in CLOUD_PROVIDERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose Anthropic, OpenAI or Google Gemini.")
    chat_model = body.chat_model.strip()
    if body.enabled:
        if not chat_model:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose the cloud model that should answer.")
        has_key = db.scalar(
            select(ProviderKey.id).where(ProviderKey.user_id == user.id, ProviderKey.provider == body.provider)
        )
        if not has_key:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Add an API key for {PROVIDERS[body.provider].label} before switching to it.",
            )
    switch = CloudSwitch(
        enabled=body.enabled,
        provider=body.provider,
        chat_model=chat_model,
        classifier_model=body.classifier_model.strip(),
    )
    save_cloud_switch(db, user.id, switch)
    return _out(db, user, switch)
