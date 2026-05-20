"""Grid symbol scanner — strict economics gates."""

from __future__ import annotations

from backend.api.grid_symbol_scanner import (
    GridScanCriteria,
    evaluate_symbol_for_grid,
    rank_grid_symbol_suggestions,
)


def _exchange_row(sym: str, base: str) -> dict:
    return {
        "symbol": sym,
        "baseAsset": base,
        "status": "TRADING",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.00001000"},
            {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1"},
            {"filterType": "NOTIONAL", "minNotional": "5", "notional": "5"},
        ],
    }


def _ticker(
    sym: str,
    *,
    last: float,
    qvol: float,
    chg: float,
    hi: float,
    lo: float,
) -> dict:
    return {
        "symbol": sym,
        "lastPrice": str(last),
        "quoteVolume": str(qvol),
        "priceChangePercent": str(chg),
        "highPrice": str(hi),
        "lowPrice": str(lo),
    }


def test_evaluate_doge_like_passes():
    hit = evaluate_symbol_for_grid(
        symbol="DOGEUSDT",
        base_asset="DOGE",
        ticker=_ticker(
            "DOGEUSDT",
            last=0.15,
            qvol=80_000_000,
            chg=4.2,
            hi=0.155,
            lo=0.145,
        ),
        filters={"tick_size": 0.00001, "step_size": 1.0, "min_qty": 1.0, "min_notional": 5.0},
        allocated_capital=120.0,
    )
    assert hit is not None
    assert hit.symbol == "DOGEUSDT"
    assert hit.generator_count >= 6
    assert hit.checks["economicsOk"] is True


def test_rejects_stable_base():
    assert (
        evaluate_symbol_for_grid(
            symbol="USDCUSDT",
            base_asset="USDC",
            ticker=_ticker("USDCUSDT", last=1.0, qvol=99_000_000, chg=0.1, hi=1.001, lo=0.999),
            filters={"tick_size": 0.0001, "step_size": 1.0, "min_qty": 1.0, "min_notional": 5.0},
            allocated_capital=100.0,
        )
        is None
    )


def test_rejects_low_liquidity():
    assert (
        evaluate_symbol_for_grid(
            symbol="XYZUSDT",
            base_asset="XYZ",
            ticker=_ticker("XYZUSDT", last=1.0, qvol=100_000, chg=5.0, hi=1.05, lo=0.95),
            filters={"tick_size": 0.01, "step_size": 0.01, "min_qty": 0.01, "min_notional": 5.0},
            allocated_capital=100.0,
        )
        is None
    )


def test_rank_returns_sorted_by_score():
    rows = [
        _exchange_row("DOGEUSDT", "DOGE"),
        _exchange_row("BTCUSDT", "BTC"),
    ]
    tickers = [
        _ticker("DOGEUSDT", last=0.15, qvol=60_000_000, chg=5.0, hi=0.16, lo=0.14),
        _ticker("BTCUSDT", last=60_000, qvol=2_000_000_000, chg=2.0, hi=61_000, lo=59_000),
    ]
    results, rejected = rank_grid_symbol_suggestions(
        exchange_symbols=rows,
        tickers=tickers,
        allocated_capital=150.0,
        limit=5,
    )
    assert len(results) >= 1
    assert results[0].score >= (results[-1].score if len(results) > 1 else 0)
    assert rejected >= 0


def test_tight_capital_rejects_many_lines():
    hit = evaluate_symbol_for_grid(
        symbol="DOGEUSDT",
        base_asset="DOGE",
        ticker=_ticker("DOGEUSDT", last=0.15, qvol=20_000_000, chg=3.0, hi=0.155, lo=0.145),
        filters={"tick_size": 0.00001, "step_size": 1.0, "min_qty": 1.0, "min_notional": 5.0},
        allocated_capital=30.0,
        criteria=GridScanCriteria(band_span_pct=3.5),
    )
    assert hit is None or hit.generator_count <= 3
