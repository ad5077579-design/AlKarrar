"""Grid execution safety: allocation cap, fill-gated trailing, strict crosses."""

from __future__ import annotations

import pytest

from backend.main_engine import Side
from backend.strategies.alkarrar_pro_shifting_grid import (
    AlKarrarProShiftingGridStrategy,
    LineTrailPhase,
    LineTrailState,
    spot_order_filled,
)
from backend.strategies.virtual_grid_book import VirtualGridBook, VirtualGridLine


@pytest.fixture
def strategy() -> AlKarrarProShiftingGridStrategy:
    s = AlKarrarProShiftingGridStrategy(exchange=None)
    s._running = True
    s._symbol = "TRXUSDT"
    s._filters = {"tick_size": 0.0001, "step_size": 0.1, "min_qty": 0.1, "min_notional": 5.0}
    s._allocated_capital_usdt = 100.0
    s._ram.generatorCount = 10
    s._ram.initialCapital = 100.0
    s._ram.cumulative_realized_usdt = 0.0
    s._ram.order_quantity_effective = 50.0
    s._ram.generatorLower = 0.10
    s._ram.generatorUpper = 0.20
    s._ram.last_price = 0.15
    return s


def test_usdt_per_line_budget_from_allocated_only(strategy: AlKarrarProShiftingGridStrategy) -> None:
    assert strategy._usdt_per_line_budget() == pytest.approx(10.0)


def test_qty_capped_when_line_price_below_mark(strategy: AlKarrarProShiftingGridStrategy) -> None:
    """Stale low grid price must not inflate base qty vs allocated slice."""
    ln = VirtualGridLine(
        line_index=0,
        price=0.10,
        price_s="0.1000",
        qty_s="500",
        side=Side.BUY,
    )
    strategy._ram.last_price = 0.15
    qty = strategy._qty_for_virtual_line(ln, mark=0.15)
    assert qty * 0.15 <= 10.0 * 1.02 + 1e-9


def test_qty_for_virtual_line_uses_slice_not_stale_qty_s(
    strategy: AlKarrarProShiftingGridStrategy,
) -> None:
    ln = VirtualGridLine(
        line_index=0,
        price=2.0,
        price_s="2.0000",
        qty_s="999",
        side=Side.BUY,
    )
    qty = strategy._qty_for_virtual_line(ln, mark=2.0)
    assert qty * 2.0 <= 10.0 * 1.02 + 1e-9


def test_crossed_lines_strict_single_cross() -> None:
    book = VirtualGridBook(symbol="XRPUSDT")
    book.register(
        line_index=0,
        price=1.5,
        price_s="1.5000",
        qty_s="1",
        side=Side.BUY,
    )
    assert book.crossed_lines(1.6, 1.5, first_side=Side.BUY)
    assert not book.crossed_lines(1.5, 1.5, first_side=Side.BUY)
    assert not book.crossed_lines(1.4, 1.5, first_side=Side.BUY)


def test_trailing_requires_exchange_fill_confirmed(strategy: AlKarrarProShiftingGridStrategy) -> None:
    strategy._ram.line_trail[0] = LineTrailState(
        phase=LineTrailPhase.idle,
        tp_level=1.0,
        lock_floor=1.0,
        exchange_fill_confirmed=False,
    )
    audits: list[str] = []
    strategy._audit = lambda et, **kw: audits.append(et)  # type: ignore[method-assign]
    strategy._trailing_eval(1.2)
    assert strategy._ram.line_trail[0].phase == LineTrailPhase.idle
    assert audits == []

    strategy._ram.line_trail[0].exchange_fill_confirmed = True
    strategy._lines_with_confirmed_buy.add(0)
    strategy._trailing_eval(1.2)
    assert strategy._ram.line_trail[0].phase == LineTrailPhase.lock_profit


def test_trailing_blocked_without_confirmed_buy_line(strategy: AlKarrarProShiftingGridStrategy) -> None:
    strategy._ram.line_trail[0] = LineTrailState(
        phase=LineTrailPhase.trailing,
        tp_level=1.0,
        lock_floor=1.0,
        trail_peak=1.2,
        exchange_fill_confirmed=True,
        trailing_audit_done=True,
    )
    strategy._lines_with_confirmed_buy.clear()
    strategy._trailing_eval(1.3)
    assert strategy._ram.line_trail[0].phase == LineTrailPhase.idle
    assert strategy._ram.line_trail[0].trail_peak == 0.0


def test_trailing_audit_once_per_cycle(strategy: AlKarrarProShiftingGridStrategy) -> None:
    strategy._ram.line_trail[0] = LineTrailState(
        phase=LineTrailPhase.lock_profit,
        tp_level=1.0,
        lock_floor=1.0,
        exchange_fill_confirmed=True,
    )
    strategy._lines_with_confirmed_buy.add(0)
    audits: list[str] = []
    strategy._audit = lambda et, **kw: audits.append(et)  # type: ignore[method-assign]
    strategy._trailing_eval(1.1)
    strategy._trailing_eval(1.2)
    assert audits.count("TRAILING_STARTED") == 1
    assert strategy._ram.line_trail[0].phase == LineTrailPhase.trailing


def test_spot_order_filled() -> None:
    assert spot_order_filled({"status": "FILLED", "executedQty": "1.0"})
    assert not spot_order_filled({"status": "NEW"})
    assert not spot_order_filled({"status": "EXPIRED", "executedQty": "1.0"})
    assert not spot_order_filled({"status": "PARTIALLY_FILLED", "executedQty": "0.5"})
