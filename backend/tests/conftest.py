"""Pytest bootstrap: isolated SQLite under a temp data dir (no shared dev DB)."""

from __future__ import annotations

import asyncio
import os
import tempfile

# Must run before test modules import ``backend.database`` (engine binds at import).
if not (os.environ.get("ALKARRAR_DATA_DIR") or "").strip():
    os.environ["ALKARRAR_DATA_DIR"] = tempfile.mkdtemp(prefix="alkarrar_pytest_")


def pytest_configure(config) -> None:  # noqa: ANN001
    import pytest

    config.addinivalue_line(
        "markers",
        "financial: execution safety, isolation, resume guards, portfolio risk",
    )


def pytest_sessionstart(session) -> None:  # noqa: ANN001
    from backend.database import init_db

    asyncio.run(init_db())
