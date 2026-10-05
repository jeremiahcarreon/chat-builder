"""The running version, baked into the image at build time (APP_VERSION)."""

import os
from pathlib import Path


def _from_file() -> str:
    # Development: read VERSION from the repository root.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "VERSION"
        if candidate.is_file():
            return candidate.read_text().strip() + "-dev"
    return "0.0.0-dev"


__version__ = os.environ.get("APP_VERSION") or _from_file()
