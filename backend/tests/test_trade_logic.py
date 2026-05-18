"""Unit / stress tests — spot realized ledger, dynamic compound sizing, upper breakout + trailing continuity."""

from __future__ import annotations

import pytest

from backend.api.spot_realized_ledger import SpotGridRealizedLedger
from backend.main_engine import Side
from backend.strategies.alkarrar_pro_shifting_grid import (
    AlKarrarProShiftingGridStrategy,
    LineTrailPhase,
    LineTrailState,
    ShiftingGridRAM,
)


def _buy(tid: int, price: float, qty: float) -> dict:
    return {
        "id": tid,
        "symbol": "DOGEUSDT",
        "price": str(price),
        "qty": str(qty),
        "quoteQty": str(price * qty),
        "isBuyer": True,
        "commission": "0",
        "commissionAsset": "USDT",
    }


def _sell(tid: int, price: float, qty: float) -> dict:
    return {
        "id": tid,
        "symbol": "DOGEUSDT",
        "price": str(price),
        "qty": str(qty),
        "quoteQty": str(price * qty),
        "isBuyer": False,
        "commission": "0",
        "commissionAsset": "USDT",
    }


def test_fifo_realized_buy_then_sell_positive():
    ledger = SpotGridRealizedLedger()
    d = ledger.ingest_many([_buy(1, 100.0, 1.0), _sell(2, 110.0, 1.0)])
    assert d == pytest.approx(10.0, rel=1e-6)
    assert len(ledger.buy_lots) == 0


def test_fifo_idempotent_double_ingest():
    ledger = SpotGridRealizedLedger()
    batch = [_buy(10, 50.0, 2.0), _sell(11, 52.0, 2.0)]
    assert ledger.ingest_many(batch) > 0
    assert ledger.ingest_many(batch) == 0.0


def test_sell_without_session_buy_does_not_fake_profit():
    """Wallet dump must not count full proceeds as grid PnL."""
    ledger = SpotGridRealizedLedger(session_isolated=True)
    d = ledger.ingest_many([_sell(99, 20.0, 1.0)])
    assert d == pytest.approx(0.0, abs=1e-9)
    assert ledger.open_inventory_base_qty() == 0.0


def test_realized_pnl_formula_matches_fifo():
    ledger = SpotGridRealizedLedger()
    d = ledger.ingest_many([_buy(1, 100.0, 1.0), _sell(2, 110.0, 1.0)])
    explicit = SpotGridRealizedLedger.realized_pnl_usdt(
        sell_qty=1.0,
        sell_price=110.0,
        buy_unit_cost=100.0,
    )
    assert d == pytest.approx(explicit, rel=1e-6)


def test_validate_grid_economics_rejects_sol_style_config():
    from backend.api.spot_realized_ledger import validate_grid_economics

    with pytest.raises(ValueError, match="حجم الخط"):
        validate_grid_economics(
            generator_upper=87.0,
            generator_lower=86.5,
            generator_count=50,
            allocated_capital=500.0,
        )


def test_validate_band_rejects_cross_symbol_contamination():
    from backend.api.spot_realized_ledger import validate_band_matches_symbol_mark

    with pytest.raises(ValueError, match="لا يطابق"):
        validate_band_matches_symbol_mark(
            generator_upper=87.0,
            generator_lower=86.5,
            mark_price=0.28,
            symbol="TRXUSDT",
        )


def test_band_from_mark_span_tiny_price():
    from backend.api.spot_realized_ledger import band_from_mark_span, band_mid_deviation_pct

    mark = 3.65e-06
    lo, hi = band_from_mark_span(mark, span_pct=3.5)
    assert lo < hi < mark * 1.1
    assert lo > 0
    assert band_mid_deviation_pct(generator_upper=hi, generator_lower=lo, mark_price=mark) < 0.02


def test_validate_trailing_offset_rejects_tiny_offset():
    from backend.api.spot_realized_ledger import validate_trailing_offset

    with pytest.raises(ValueError, match="trailingOffset"):
        validate_trailing_offset(trailing_offset=0.0007, mark_price=86.0)


def test_validate_grid_economics_rejects_tight_band():
    from backend.api.spot_realized_ledger import validate_grid_economics

    with pytest.raises(ValueError, match="المسافة"):
        validate_grid_economics(
            generator_upper=1.006,
            generator_lower=1.0,
            generator_count=50,
            allocated_capital=600.0,
        )

    with pytest.raises(ValueError, match="حجم الخط"):
        validate_grid_economics(
            generator_upper=1.1,
            generator_lower=1.0,
            generator_count=20,
            allocated_capital=100.0,
        )


def _bare_strategy() -> AlKarrarProShiftingGridStrategy:
    """Instance without __init__ / on_start — RAM methods only."""
    s = AlKarrarProShiftingGridStrategy.__new__(AlKarrarProShiftingGridStrategy)
    s._first_side = Side.BUY  # noqa: SLF001
    s._lift_above_offset = 0.01  # noqa: SLF001
    s._trailing_stop_pct = 0.008  # noqa: SLF001
    s._boundary_reinvest_frac = 0.25  # noqa: SLF001
    s._lot_expand_step_pct = 0.05  # noqa: SLF001
    s._last_available_usdt = 100.0  # noqa: SLF001
    s._filters = {  # noqa: SLF001
        "tick_size": 0.00001,
        "step_size": 0.00001,
        "min_qty": 0.0,
        "min_notional": 0.0,
    }
    s._compound_resize_pct = 0.01  # noqa: SLF001
    s._running = False  # noqa: SLF001
    s._virtual_book = None  # noqa: SLF001
    s._bot_id = "default"  # noqa: SLF001
    s._symbol = "DOGEUSDT"  # noqa: SLF001
    return s


def test_upper_breakout_shift_preserves_trailing_peak():
    """Violent upper breakout: band shifts; trailing line keeps absolute trail_peak (no full reset)."""
    s = _bare_strategy()
    s._ram = ShiftingGridRAM(  # noqa: SLF001
        generatorUpper=100.0,
        generatorLower=98.0,
        generatorCount=5,
        initialCapital=250.0,
        trailingOffset=0.1,
        compoundingFactor=1.0,
        order_quantity_base=0.1,
        order_quantity_effective=0.1,
    )
    peak = 105.2
    idx = 3
    s._ram.line_trail[idx] = LineTrailState(  # noqa: SLF001
        phase=LineTrailPhase.trailing,
        tp_level=100.42,
        lock_floor=100.3,
        trail_peak=peak,
    )

    shock = 107.0
    assert s._lift_eval_and_mutate_ram(shock) is True

    assert s._ram.generatorUpper == pytest.approx(shock)  # noqa: SLF001
    assert (s._ram.generatorUpper - s._ram.generatorLower) == pytest.approx(2.0)  # noqa: SLF001

    st = s._ram.line_trail[idx]  # noqa: SLF001
    assert st.phase == LineTrailPhase.trailing
    assert st.trail_peak == pytest.approx(peak)

    s._ram.line_trail[0] = LineTrailState(  # noqa: SLF001
        phase=LineTrailPhase.idle,
        tp_level=98.0,
        lock_floor=0.0,
        trail_peak=0.0,
    )
    s._lift_eval_and_mutate_ram(109.5)
    lv0 = s._rebuild_levels()[0]
    assert s._ram.line_trail[0].tp_level == pytest.approx(lv0 + 0.1)  # noqa: SLF001
    assert s._ram.line_trail[idx].trail_peak == pytest.approx(peak)  # noqa: SLF001


def test_dynamic_compound_does_not_expand_generator_count():
    s = _bare_strategy()
    s._ram = ShiftingGridRAM(  # noqa: SLF001
        generatorUpper=105.0,
        generatorLower=95.0,
        generatorCount=4,
        initialCapital=100.0,
        trailingOffset=0.1,
        compoundingFactor=1.0,
        order_quantity_base=0.05,
        order_quantity_effective=0.05,
    )
    count_before = s._ram.generatorCount
    s._ram.cumulative_realized_usdt = 5.0
    s._compound_resize_pct = 0.01
    changed = s.refresh_order_size_from_capital(deploy_usdt=200.0, mark=100.0, reason="test", force=True)
    assert changed is True
    assert s._ram.generatorCount == count_before
    assert s._ram.order_quantity_effective >= 0.0

    s._ram.cumulative_realized_at_last_resize = 5.0
    s._ram.cumulative_realized_usdt = 5.5
    assert s.refresh_order_size_from_capital(deploy_usdt=200.0, mark=100.0) is False
    s._ram.cumulative_realized_usdt = 8.0
    assert s.refresh_order_size_from_capital(deploy_usdt=200.0, mark=100.0, force=False) is True


def test_asymmetric_classify_only_buys_strictly_below_mark():
    s = _bare_strategy()
    s._ram = ShiftingGridRAM(  # noqa: SLF001
        generatorUpper=110.0,
        generatorLower=100.0,
        generatorCount=5,
        initialCapital=100.0,
        trailingOffset=0.1,
        compoundingFactor=1.0,
        order_quantity_base=0.1,
        order_quantity_effective=0.1,
    )
    mark = 110.0
    buys, sells = s._classify_levels_by_mark(mark, buys_only_strict_below=True)  # noqa: SLF001
    assert sells == []
    assert buys
    assert all(px < mark for _, px in buys)


def test_recenter_sets_asymmetric_shift_flag():
    s = _bare_strategy()
    s._ram = ShiftingGridRAM(  # noqa: SLF001
        generatorUpper=100.0,
        generatorLower=98.0,
        generatorCount=5,
        initialCapital=100.0,
        trailingOffset=0.1,
        compoundingFactor=1.0,
        order_quantity_base=0.1,
        order_quantity_effective=0.1,
    )
    assert s._recenter_grid_on_pivot(107.0, reason="price_breakout") is True  # noqa: SLF001
    assert s._asymmetric_shift_active is True  # noqa: SLF001


def test_trailing_equity_stop_triggers_at_drawdown():
    from backend.main_engine import RiskLimits, RiskManager

    rm = RiskManager(RiskLimits(max_notional_usdt=1e6, max_order_notional_usdt=1e6, trailing_equity_drawdown_pct=0.10))
    rm.seed_peak_equity(1000.0)
    assert rm.check_trailing_equity_stop(950.0) is False
    assert rm.check_trailing_equity_stop(899.0) is True
