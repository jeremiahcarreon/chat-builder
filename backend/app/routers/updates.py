"""Update status for the admin panel.

The updater container checks GitHub for releases and writes its status to a
shared volume; this reads it and lets the admin ask for an immediate update.
Installs without the updater (development, manual setups) report "unmanaged".
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.deps import CurrentUser
from app.version import __version__

router = APIRouter(prefix="/api/system/updates", tags=["system"])


def _state_dir() -> Path | None:
    value = os.environ.get("UPDATE_STATE_DIR")
    return Path(value) if value else None


class UpdateStatus(BaseModel):
    current: str
    managed: bool
    latest: str | None = None
    available: bool = False
    status: str = "unknown"
    message: str = ""
    checked_at: datetime | None = None
    auto_update_at: datetime | None = None
    release_url: str | None = None
    requested: bool = False
    # The request has waited longer than the updater ever should (it checks every 30s).
    stalled: bool = False


STALLED_AFTER_SECONDS = 300


def _age_seconds(path: Path) -> float:
    try:
        return datetime.now(UTC).timestamp() - path.stat().st_mtime
    except OSError:
        return 0.0


def _timestamp(value) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if isinstance(value, (int, float)) else None


def read_status() -> UpdateStatus:
    directory = _state_dir()
    state_file = directory / "update-state.json" if directory else None
    if state_file is None or not state_file.is_file():
        return UpdateStatus(current=__version__, managed=False)
    try:
        state = json.loads(state_file.read_text())
    except (OSError, ValueError):
        return UpdateStatus(current=__version__, managed=True, status="unknown", message="Update status unreadable")
    latest = state.get("latest") or None
    requested = (directory / "update-requested").exists() and state.get("status") != "updating"
    return UpdateStatus(
        current=__version__,
        managed=True,
        latest=latest,
        available=state.get("status") in ("available", "updating", "failed") and latest not in (None, __version__),
        status=state.get("status", "unknown"),
        message=state.get("message", ""),
        checked_at=_timestamp(state.get("checked_at")),
        auto_update_at=_timestamp(state.get("auto_update_at")),
        release_url=state.get("release_url") if latest else None,
        requested=requested,
        stalled=requested and _age_seconds(directory / "update-requested") > STALLED_AFTER_SECONDS,
    )


@router.get("")
def update_status(_: CurrentUser) -> UpdateStatus:
    return read_status()


@router.post("/apply")
def apply_update(user: CurrentUser) -> UpdateStatus:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can install updates")
    current = read_status()
    if not current.managed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Updates aren't managed on this install.")
    if not current.available:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No update is available.")
    # The updater checks for this file every 30 seconds.
    (_state_dir() / "update-requested").write_text(datetime.now(UTC).isoformat())
    return read_status()
