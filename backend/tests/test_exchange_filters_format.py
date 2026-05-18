"""LOT_SIZE / NOTIONAL formatting for compound sizing."""

from __future__ import annotations

from backend.core.exchange_filters import format_base_qty_from_usdt_slice, normalize_order


def test_format_base_qty_respects_lot_step():
    filters = {
        "tick_size": 0.0001,
        "step_size": 1.0,
        "min_qty": 1.0,
        "min_notional": 5.0,
    }
    qty_f, qty_s = format_base_qty_from_usdt_slice(
        usdt_per_line=15.43287,
        mark=0.15,
        filters=filters,
    )
    assert qty_f >= 1.0
    _, check = normalize_order(0.15, qty_f, filters)
    assert qty_s == check
