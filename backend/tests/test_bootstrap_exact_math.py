"""وحدة اختبار: حجم شراء الماركت الصارم مقابل خطوط البيع (بدون شبكة)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.strategies.alkarrar_pro_shifting_grid import market_buy_quantity_string_covers_net_base


@pytest.mark.parametrize(
    ("mark", "need", "fee"),
    [
        (2.0, Decimal("10"), Decimal("0.001")),
        (100.0, Decimal("2.5"), Decimal("0.001")),
    ],
)
def test_market_buy_covers_net_after_fee(mark: float, need: Decimal, fee: Decimal) -> None:
    filters = {
        "tick_size": 0.0001,
        "step_size": 0.1,
        "min_qty": 0.1,
        "min_notional": 5.0,
    }
    q_s, note = market_buy_quantity_string_covers_net_base(
        mark,
        filters=filters,
        net_base_need=need,
        fee_take_from_received_base=fee,
    )
    assert q_s is not None, note
    gross = Decimal(q_s)
    assert gross * (Decimal(1) - fee) >= need, (q_s, note, gross * (Decimal(1) - fee), need)


def test_no_net_means_no_market_qty() -> None:
    q, note = market_buy_quantity_string_covers_net_base(
        1.0,
        filters={"tick_size": 0.01, "step_size": 1, "min_qty": 1, "min_notional": 5},
        net_base_need=Decimal(0),
        fee_take_from_received_base=Decimal("0.001"),
    )
    assert q is None
    assert "no-net" in note
