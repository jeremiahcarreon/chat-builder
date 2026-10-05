"""Chat Builder is one shared workspace: admins see and manage every app and share
the same provider keys and settings. These are stored under the workspace owner,
the first admin account (created from ADMIN_EMAIL)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


def workspace_owner_id(db: Session) -> uuid.UUID:
    owner = db.scalar(select(User.id).where(User.is_admin.is_(True)).order_by(User.created_at).limit(1))
    if owner is None:
        raise RuntimeError("No admin account exists")
    return owner
