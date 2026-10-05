import json

import pytest

from app.routers import updates


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("UPDATE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(updates, "__version__", "0.1.0")
    return tmp_path


def _write(directory, **state):
    (directory / "update-state.json").write_text(json.dumps(state))


def test_unmanaged_without_updater(tmp_path, monkeypatch):
    monkeypatch.delenv("UPDATE_STATE_DIR", raising=False)
    assert updates.read_status().managed is False


def test_available_update(state_dir):
    _write(state_dir, latest="0.2.0", status="available", checked_at=1_700_000_000, auto_update_at=1_700_086_400,
           release_url="https://github.com/x/y/releases/tag/v0.2.0")
    status = updates.read_status()
    assert status.managed and status.available
    assert status.latest == "0.2.0"
    assert status.auto_update_at.timestamp() == 1_700_086_400


def test_up_to_date(state_dir):
    _write(state_dir, latest="0.1.0", status="idle", checked_at=1_700_000_000, auto_update_at=None)
    status = updates.read_status()
    assert status.managed and not status.available


def test_corrupt_state_is_reported_not_raised(state_dir):
    (state_dir / "update-state.json").write_text("{not json")
    status = updates.read_status()
    assert status.managed and not status.available


def test_request_flag(state_dir):
    _write(state_dir, latest="0.2.0", status="available")
    (state_dir / "update-requested").write_text("now")
    assert updates.read_status().requested


def test_stalled_request(state_dir):
    import os
    import time

    _write(state_dir, latest="0.2.0", status="available")
    request = state_dir / "update-requested"
    request.write_text("now")
    assert not updates.read_status().stalled
    old = time.time() - 600
    os.utime(request, (old, old))
    assert updates.read_status().stalled
