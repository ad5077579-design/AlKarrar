"""Per-grid allocation, deploy capital, and isolated trailing stop."""

from __future__ import annotations

import asyncio

import pytest

from backend.api.portfolio_risk import (
    clear_grid_risk_slot,
    maybe_trailing_equity_stop_for_grid,
    reset_trailing_equity_baseline_for_grid,
)
from backend.api.spot_realized_ledger import SpotGridRealizedLedger
from backend.strategies.alkarrar_pro_shifting_grid import AlKarrarProShiftingGridStrategy, ShiftingGridRAM


def test_effective_deploy_includes_only_grid_realized():
    s = AlKarrarProShiftingGridStrategy(exchange=None)
    s._ram = ShiftingGridRAM(  # noqa: SLF001
        generatorUpper=110.0,
        generatorLower=100.0,
        generatorCount=5,
        initialCapital=50.0,
        trailingOffset=0.1,
        compoundingFactor=1.0,
        order_quantity_base=0.1,
        order_quantity_effective=0.1,
    )
    s._allocated_capital_usdt = 50.0
    s._ram.cumulative_realized_usdt = 7.5
    assert s.effective_deploy_usdt() == pytest.approx(57.5)


def test_ledger_unrealized_isolated():
    led = SpotGridRealizedLedger()
    led.ingest_normalized(
        {
            "id": 1,
            "symbol": "BTCUSDT",
            "price": 100.0,
            "qty": 1.0,
            "quoteQty": 100.0,
            "side": "BUY",
            "commission": 0.0,
            "commissionAsset": "USDT",
        }
    )
    assert led.unrealized_pnl_usdt(105.0) == pytest.approx(5.0)


def test_trailing_stop_per_symbol_only(monkeypatch):
    clear_grid_risk_slot("BTCUSDT")
    clear_grid_risk_slot("ETHUSDT")
    reset_trailing_equity_baseline_for_grid("BTCUSDT", 100.0)

    called: list[str] = []

    async def _fake_emergency(bot_id: str, *, symbol: str | None = None) -> dict:
        called.append(str(symbol))
        return {"status": "ok"}

    monkeypatch.setattr(
        "backend.api.emergency_service.execute_emergency_stop",
        _fake_emergency,
    )

    triggered = asyncio.run(
        maybe_trailing_equity_stop_for_grid(
            symbol="BTCUSDT",
            grid_equity_usdt=89.0,
            bot_id="default",
        )
    )
    assert triggered is True
    assert called == ["BTCUSDT"]
