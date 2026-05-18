"""Grid snapshot store + resume settings builder."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytestmark = pytest.mark.financial

from backend.api.grid_snapshot_store import (
    build_grid_settings_from_snapshot,
    list_resumable_snapshots,
    set_auto_resume,
    write_snapshot_payload,
)


def test_snapshot_auto_resume_roundtrip(tmp_path: Path) -> None:
    async def _run() -> None:
        db = tmp_path / "trader.db"
        payload = {
            "snapshotVersion": 1,
            "autoResume": True,
            "symbol": "DOGEUSDT",
            "generatorUpper": 0.11,
            "generatorLower": 0.10,
            "generatorCount": 12,
            "initialCapital": 500.0,
            "gridSettings": {
                "symbol": "DOGEUSDT",
                "generatorUpper": 0.11,
                "generatorLower": 0.10,
                "generatorCount": 12,
                "allocatedCapital": 500.0,
                "trailingOffset": 0.0002,
            },
            "virtualGrid": [
                {
                    "line_index": 0,
                    "price": 0.105,
                    "price_s": "0.10500",
                    "qty_s": "100",
                    "side": "buy",
                    "armed": True,
                    "triggered": False,
                }
            ],
        }
        await write_snapshot_payload("default", "DOGEUSDT", payload, path=db)
        rows = await list_resumable_snapshots("default", path=db)
        assert len(rows) == 1
        settings = build_grid_settings_from_snapshot(rows[0])
        assert settings is not None
        assert settings["symbol"] == "DOGEUSDT"
        assert settings["resumeFromSnapshot"]["autoResume"] is True

        await set_auto_resume("default", "DOGEUSDT", enabled=False, path=db)
        rows2 = await list_resumable_snapshots("default", path=db)
        assert rows2 == []

    asyncio.run(_run())


def test_build_settings_rejects_non_resumable() -> None:
    settings = build_grid_settings_from_snapshot(
        {"symbol": "X", "payload": {"autoResume": False}, "updated_ms": 0}
    )
    assert settings is None
