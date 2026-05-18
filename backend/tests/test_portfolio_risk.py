"""Portfolio trailing equity stop integration."""

from __future__ import annotations

import pytest

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

    pr.portfolio_risk.seed_peak_equity(1000.0)
    pr._emergency_triggered = True  # noqa: SLF001
    pr.reset_trailing_equity_baseline(720.0)
    assert pr.trailing_emergency_latched() is False
    assert pr.portfolio_risk.state.peak_equity_usdt == pytest.approx(720.0)
    assert pr.portfolio_risk.check_trailing_equity_stop(720.0) is False
