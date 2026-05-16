"""Unit / stress tests — spot realized ledger, hybrid injection, upper breakout + trailing continuity."""

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


def _bare_strategy() -> AlKarrarProShiftingGridStrategy:
    """Instance without __init__ / on_start — RAM methods only."""
    s = AlKarrarProShiftingGridStrategy.__new__(AlKarrarProShiftingGridStrategy)
    s._first_side = Side.BUY  # noqa: SLF001
    s._lift_above_offset = 0.01  # noqa: SLF001
    s._trailing_stop_pct = 0.008  # noqa: SLF001
    s._profit_injection_mode = "expand_count"  # noqa: SLF001
    s._hybrid_line_cap = True  # noqa: SLF001
    s._max_generator_count = 12  # noqa: SLF001
    s._boundary_reinvest_frac = 0.25  # noqa: SLF001
    s._lot_expand_step_pct = 0.05  # noqa: SLF001
    s._filters = {"tick_size": 0.00001}  # noqa: SLF001
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
        compoundingFactor=0.05,
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


def test_hybrid_injection_expand_until_max_then_compound():
    s = _bare_strategy()
    s._max_generator_count = 5  # noqa: SLF001
    s._ram = ShiftingGridRAM(  # noqa: SLF001
        generatorUpper=105.0,
        generatorLower=95.0,
        generatorCount=4,
        initialCapital=100.0,
        trailingOffset=0.1,
        compoundingFactor=0.05,
        order_quantity_base=0.05,
        order_quantity_effective=0.05,
        cumulative_realized_usdt=999.0,
        injections_done=0,
    )
    qty0 = s._ram.order_quantity_effective
    s._profit_injection_eval()
    assert s._ram.generatorCount == 5
    qty_mid = s._ram.order_quantity_effective

    s._profit_injection_eval()
    assert s._ram.generatorCount == 5
    assert qty_mid != pytest.approx(qty0)
    assert s._ram.order_quantity_effective == pytest.approx(qty_mid * 1.05)
