"""In-memory grid ledger flush/freeze rules."""

from __future__ import annotations

from backend.api.grid_live_ledger import grid_live_ledger


def test_manual_flush_clears_ledger():
    grid_live_ledger.begin_session("DOGEUSDT", bot_id="default")
    grid_live_ledger.append(
        "DOGEUSDT",
        action_type="ORDER_BUY",
        trigger_reason="test",
    )
    assert grid_live_ledger.snapshot("DOGEUSDT")["count"] == 1
    grid_live_ledger.flush_manual_stop("DOGEUSDT")
    assert grid_live_ledger.snapshot("DOGEUSDT")["count"] == 0


def test_emergency_freeze_keeps_entries_until_clear():
    grid_live_ledger.begin_session("BTCUSDT", bot_id="default")
    grid_live_ledger.append("BTCUSDT", action_type="GRID_START", trigger_reason="test")
    grid_live_ledger.freeze("BTCUSDT", reason="emergency_stop")
    snap = grid_live_ledger.snapshot("BTCUSDT")
    assert snap["frozen"] is True
    assert snap["count"] == 1
    assert grid_live_ledger.clear_user("BTCUSDT") is True
    assert grid_live_ledger.snapshot("BTCUSDT")["count"] == 0


def test_append_dedupes_same_order_id_within_window():
    grid_live_ledger.begin_session("XRPUSDT", bot_id="default")
    grid_live_ledger.append(
        "XRPUSDT",
        action_type="ORDER_BUY",
        trigger_reason="virtual_grid_fill",
        target_price=1.4127,
        fill_price=1.4127,
        quantity=9.9,
        extra={"orderId": 1467224562},
    )
    grid_live_ledger.append(
        "XRPUSDT",
        action_type="ORDER_BUY",
        trigger_reason="virtual_grid_fill",
        target_price=1.4127,
        fill_price=1.4126,
        quantity=9.9,
        extra={"orderId": 1467224562},
    )
    assert grid_live_ledger.snapshot("XRPUSDT")["count"] == 1


def test_fill_price_prefers_fills_over_limit_price():
    from backend.api.grid_live_ledger import fill_price_from_order_response

    res = {
        "price": "529.77",
        "executedQty": "0.048",
        "fills": [{"price": "529.67", "qty": "0.048"}],
    }
    assert fill_price_from_order_response(res, 0.0) == 529.67


def test_fill_price_uses_cumulative_quote_qty():
    from backend.api.grid_live_ledger import fill_price_from_order_response

    res = {
        "price": "528.24",
        "executedQty": "0.048",
        "cummulativeQuoteQty": "25.35456",
    }
    px = fill_price_from_order_response(res, 0.0)
    assert abs(px - (25.35456 / 0.048)) < 1e-6


def test_system_error_skips_ioc_miss_contexts():
    from types import SimpleNamespace

    from backend.api.grid_live_ledger import log_from_audit_event

    grid_live_ledger.begin_session("TRXUSDT", bot_id="default")
    strat = SimpleNamespace(_symbol="TRXUSDT", _bot_id="default")
    log_from_audit_event(
        strat,
        "SYSTEM_ERROR",
        details={"context": "virtual_grid_fill_no_exchange_fill", "line_index": 3},
    )
    log_from_audit_event(
        strat,
        "SYSTEM_ERROR",
        details={"context": "virtual_grid_no_exchange_fill", "line_index": 3},
    )
    assert grid_live_ledger.snapshot("TRXUSDT")["count"] == 0


def test_trailing_arm_requires_confirmed_buy_line():
    from types import SimpleNamespace

    from backend.api.grid_live_ledger import log_from_audit_event

    grid_live_ledger.begin_session("TRXUSDT", bot_id="default")
    strat = SimpleNamespace(
        _symbol="TRXUSDT",
        _bot_id="default",
        _line_has_confirmed_buy=lambda idx: idx == 0,
    )
    log_from_audit_event(
        strat,
        "TRAILING_STARTED",
        details={"line_index": 5, "exchange_fill_confirmed": True},
    )
    assert grid_live_ledger.snapshot("TRXUSDT")["count"] == 0
    log_from_audit_event(
        strat,
        "TRAILING_STARTED",
        details={"line_index": 0, "exchange_fill_confirmed": True},
    )
    assert grid_live_ledger.snapshot("TRXUSDT")["count"] == 1


def test_clear_rejected_when_not_frozen():
    grid_live_ledger.begin_session("ETHUSDT", bot_id="default")
    grid_live_ledger.append("ETHUSDT", action_type="ORDER_SELL", trigger_reason="live")
    assert grid_live_ledger.clear_user("ETHUSDT") is False
