"""Concurrency: GridManager keeps independent runners per symbol (DOGE + NEAR)."""

from __future__ import annotations

import asyncio

import pytest

from backend.api.grid_manager import GridManager


def test_two_concurrent_grids(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[str, str]] = []

    async def fake_start(self: object, bot_id: str, settings: dict) -> dict:
        sym = str(settings["symbol"]).upper().replace("/", "")
        started.append((bot_id, sym))
        setattr(self, "_running", True)
        setattr(self, "_bot_id", bot_id)
        setattr(self, "_symbol", sym)
        setattr(self, "_strategy", object())
        setattr(self, "_client", None)
        setattr(self, "_filters", {})
        setattr(self, "_started_at", "test")
        setattr(self, "_last_tick_at", None)
        setattr(self, "_last_error", "")
        setattr(self, "_orders_placed", 0)
        return {"running": True}

    async def fake_stop(self: object) -> dict:
        sym = getattr(self, "_symbol", "") or ""
        setattr(self, "_running", False)
        setattr(self, "_symbol", "")
        setattr(self, "_strategy", None)
        return {"running": False, "symbol": sym}

    monkeypatch.setattr("backend.api.grid_runner.GridRunner.start", fake_start)
    monkeypatch.setattr("backend.api.grid_runner.GridRunner.stop", fake_stop)

    async def body() -> None:
        mgr = GridManager()
        common = {
            "generatorUpper": 2.0,
            "generatorLower": 1.0,
            "generatorCount": 3,
            "initialCapital": 100.0,
            "maxGeneratorCount": 10,
        }

        await mgr.start("default", {"symbol": "DOGEUSDT", **common})
        await mgr.start("default", {"symbol": "NEARUSDT", **common})

        assert sorted(mgr.active_symbols()) == ["DOGEUSDT", "NEARUSDT"]
        assert {s for _, s in started} == {"DOGEUSDT", "NEARUSDT"}

        await mgr.stop("DOGEUSDT")
        assert mgr.active_symbols() == ["NEARUSDT"]

        await mgr.stop(None)
        assert mgr.active_symbols() == []

    asyncio.run(body())
