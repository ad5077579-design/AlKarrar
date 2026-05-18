"""Filesystem anchors relative to the repository root (Docker-friendly; no hardcoded OS paths)."""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Return AlKarrar_Pro root: parent directory of the `backend` package."""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    override = (os.environ.get("ALKARRAR_DATA_DIR") or "").strip()
    p = Path(override) if override else project_root() / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p
