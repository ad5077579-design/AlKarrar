"""Portfolio trailing equity stop integration."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.financial

from backend.main_engine import RiskLimits, RiskManager


def test_peak_updates_on_new_high():
    rm = RiskManager(
        RiskLimits(
            max_notional_usdt=1_000_000.0,
            max_order_notional_usdt=500_000.0,
            trailing_equity_drawdown_pct=0.10,
        )
    )
    rm.seed_peak_equity(500.0)
    assert rm.check_trailing_equity_stop(600.0) is False
    assert rm.state.peak_equity_usdt == pytest.approx(600.0)


def test_reset_baseline_clears_emergency_latch_and_peak():
    from backend.api import portfolio_risk as pr

    symbol = "TESTUSDT"
    pr._slot(symbol).risk.seed_peak_equity(1000.0)  # noqa: SLF001
    pr._slot(symbol).emergency_latched = True  # noqa: SLF001
    assert pr.trailing_emergency_latched(symbol) is True

    pr.reset_trailing_equity_baseline_for_grid(symbol, 720.0)
    assert pr.trailing_emergency_latched(symbol) is False
    assert pr._slot(symbol).risk.state.peak_equity_usdt == pytest.approx(720.0)  # noqa: SLF001
    assert pr._slot(symbol).risk.check_trailing_equity_stop(720.0) is False  # noqa: SLF001

    pr.clear_grid_risk_slot(symbol)
