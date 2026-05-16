"""Virtual ladder re-arm after profit injection / compound."""

from __future__ import annotations

from backend.main_engine import Side
from backend.strategies.alkarrar_pro_shifting_grid import AlKarrarProShiftingGridStrategy, ShiftingGridRAM
from backend.strategies.virtual_grid_book import VirtualGridBook


def _strategy_stub() -> AlKarrarProShiftingGridStrategy:
    s = AlKarrarProShiftingGridStrategy()
    s._running = True
    s._symbol = "DOGEUSDT"
    s._virtual_book = VirtualGridBook.from_env("DOGEUSDT")
    s._filters = {"tick_size": 0.00001, "step_size": 1.0, "min_qty": 1.0, "min_notional": 1.0}
    s._quantize_hooks = lambda p, q: (f"{p:.5f}", f"{max(1, int(q))}")  # noqa: SLF001
    s._ram = ShiftingGridRAM(
        generatorUpper=0.12,
        generatorLower=0.10,
        generatorCount=4,
        initialCapital=40.0,
        trailingOffset=0.001,
        compoundingFactor=0.05,
        order_quantity_effective=50.0,
        last_price=0.11,
    )
    s._ram.line_trail = {}
    return s


def test_rearm_qty_only_refreshes_armed_quantity():
    s = _strategy_stub()
    s._virtual_book.register(
        line_index=1,
        price=0.105,
        price_s="0.10500",
        qty_s="50",
        side=Side.BUY,
    )
    s._ram.order_quantity_effective = 80.0
    s._rearm_virtual_ladder(reason="test_compound", mode="qty_only")
    ln = s._virtual_book.lines[1]
    assert ln.qty_s == "80"


def test_rearm_full_replaces_non_triggered_lines():
    s = _strategy_stub()
    s._virtual_book.register(
        line_index=0,
        price=0.115,
        price_s="0.11500",
        qty_s="50",
        side=Side.SELL,
    )
    s._virtual_book.lines[0].triggered = True
    s._ram.generatorCount = 5
    s._rearm_virtual_ladder(reason="test_expand", mode="full")
    assert 0 in s._virtual_book.lines and s._virtual_book.lines[0].triggered
    armed = [i for i, ln in s._virtual_book.lines.items() if ln.armed and not ln.triggered]
    assert len(armed) >= 2
