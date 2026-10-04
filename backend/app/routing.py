"""Which provider and model actually serve an app.

Apps built on local Ollama models can be moved to a cloud provider in one step
with the owner's "cloud switch". The app's own settings are left untouched, so
turning the switch off returns every app to its local model.
"""

import uuid
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from app.models import ChatApp, UserSetting
from app.providers import PROVIDERS

CLOUD_SWITCH_KEY = "cloud_switch"
LOCAL_PROVIDER = "ollama"
CLOUD_PROVIDERS = ("anthropic", "openai", "gemini")


@dataclass
class CloudSwitch:
    enabled: bool = False
    provider: str = "openai"
    chat_model: str = ""
    # Blank uses the provider's suggested small classifier model.
    classifier_model: str = ""

    @property
    def active(self) -> bool:
        return self.enabled and self.provider in CLOUD_PROVIDERS and bool(self.chat_model)


def load_cloud_switch(db: Session, user_id: uuid.UUID) -> CloudSwitch:
    row = db.get(UserSetting, (user_id, CLOUD_SWITCH_KEY))
    if row is None:
        return CloudSwitch()
    known = {k: v for k, v in row.value.items() if k in CloudSwitch.__dataclass_fields__}
    return CloudSwitch(**known)


def save_cloud_switch(db: Session, user_id: uuid.UUID, switch: CloudSwitch) -> None:
    row = db.get(UserSetting, (user_id, CLOUD_SWITCH_KEY))
    if row is None:
        row = UserSetting(user_id=user_id, key=CLOUD_SWITCH_KEY, value={})
        db.add(row)
    row.value = asdict(switch)
    db.commit()


def chat_model_for(app: ChatApp, switch: CloudSwitch) -> tuple[str, str]:
    """(provider, model) that answers this app's questions."""
    if switch.active and app.provider == LOCAL_PROVIDER:
        return switch.provider, switch.chat_model
    return app.provider, app.model


def classifier_for(app: ChatApp, local_choice: tuple[str, str], switch: CloudSwitch) -> tuple[str, str]:
    """(provider, model) that screens this app's messages, given its local choice."""
    provider, model = local_choice
    if switch.active and provider == LOCAL_PROVIDER:
        return switch.provider, (
            switch.classifier_model or PROVIDERS[switch.provider].default_classifier_model or switch.chat_model
        )
    return provider, model
