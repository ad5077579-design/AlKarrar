"""
Run AlKarrar_Pro_Shifting_Grid once for DOGEUSDT (Binance USD-M), then clean up.

Examples:
  python -m backend.cli.run_doge_grid_once --dry-run
  python -m backend.cli.run_doge_grid_once
  python -m backend.cli.run_doge_grid_once --micro-roundtrip

Requires ``.env`` with ``BINANCE_API_KEY`` / ``BINANCE_API_SECRET`` unless ``--dry-run``.
Default: ``BINANCE_TESTNET=true`` (set ``BINANCE_TESTNET=false`` for mainnet — not recommended here).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
from typing import Any

_log = logging.getLogger("run_doge_grid_once")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )


async def _symbol_filters(client: Any, symbol: str) -> dict[str, Any]:
    info = await client.raw.futures_exchange_info()
    symbols = info.get("symbols") if isinstance(info, dict) else None
    if not isinstance(symbols, list):
        raise RuntimeError("unexpected exchange_info")
    for row in symbols:
        if isinstance(row, dict) and row.get("symbol") == symbol:
            return row
    raise ValueError(f"symbol not listed: {symbol}")


def _quantize_qty(qty: float, step: float, min_qty: float) -> float:
    if step <= 0:
        q = max(qty, min_qty)
    else:
        q = math.floor(qty / step) * step
        if q < min_qty - 1e-12:
            q = math.ceil(min_qty / step) * step
    decimals = 8
    if step < 1 and step > 0:
        decimals = min(8, max(0, int(round(-math.log10(step))) + 2))
    return round(q, decimals)


async def _min_trade_qty(client: Any, symbol: str, price: float) -> float:
    row = await _symbol_filters(client, symbol)
    filters = row.get("filters") or []
    step = 1.0
    min_qty = 1.0
    min_notional = 5.0
    for f in filters:
        if not isinstance(f, dict):
            continue
        ft = f.get("filterType")
        if ft == "LOT_SIZE":
            step = float(f.get("stepSize", step))
            min_qty = float(f.get("minQty", min_qty))
        elif ft == "MIN_NOTIONAL":
            min_notional = float(f.get("notional", f.get("minNotional", min_notional)))
    q = max(min_qty, min_notional / max(price, 1e-12))
    return _quantize_qty(q, step, min_qty)


async def _micro_roundtrip(client: Any, symbol: str, price: float) -> None:
    qty = await _min_trade_qty(client, symbol, price)
    _log.info("micro_roundtrip qty=%s (min rules)", qty)
    await client.create_order(
        symbol=symbol,
        side="BUY",
        order_type="MARKET",
        quantity=qty,
        reduce_only=False,
    )
    await asyncio.sleep(0.75)
    await client.create_order(
        symbol=symbol,
        side="SELL",
        order_type="MARKET",
        quantity=qty,
        reduce_only=True,
    )


async def _run(args: argparse.Namespace) -> int:
    _setup_logging()
    symbol = "DOGEUSDT"
    bot_id = "doge-grid-once"

    if args.dry_run:
        _log.info("dry-run: would trade %s on testnet=%s", symbol, not args.mainnet)
        return 0

    from backend.core.binance_client import BinanceFuturesClient
    from backend.main_engine import EngineSettings
    from backend.strategies.alkarrar_pro_shifting_grid import AlKarrarProShiftingGridStrategy

    settings = EngineSettings()
    if not (settings.binance_api_key and settings.binance_api_secret):
        _log.error("Missing BINANCE_API_KEY / BINANCE_API_SECRET in environment or .env")
        return 2

    testnet = settings.binance_testnet and not args.mainnet
    legacy = settings.binance_legacy_futures_testnet
    client: Any = None
    strategy: Any = None
    try:
        client = await BinanceFuturesClient.create_for_paper_or_mainnet(
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
            paper=testnet,
            legacy_futures_testnet=legacy,
        )
        t = await client.fetch_ticker(symbol)
        price = float(
            t.get("lastPrice") or t.get("last") or t.get("price") or t.get("close") or 0
        )
        if price <= 0:
            _log.error("could not parse ticker price: %s", t)
            return 3

        band = max(price * 0.012, 1e-6)
        upper = price + band * 0.55
        lower = price - band * 0.45
        strat_settings: dict[str, Any] = {
            "symbol": symbol,
            "generatorUpper": upper,
            "generatorLower": lower,
            "generatorCount": max(3, min(args.levels, 12)),
            "initialCapital": float(args.initial_capital_usdt),
            "trailingOffset": max(price * 0.004, 1e-6),
            "compoundingFactor": 0.08,
            "lift_above_offset": max(price * 5e-5, 1e-6),
            "trailing_stop_pct": 0.012,
            "profit_injection_mode": "expand_count",
        }

        strategy = AlKarrarProShiftingGridStrategy(client)
        await strategy.on_start(bot_id, strat_settings)
        _log.info("strategy started mark≈%s band [%.6f, %.6f]", price, lower, upper)

        await asyncio.sleep(float(args.bootstrap_wait_s))

        for i in range(int(args.tick_steps)):
            bump = 1.0 + (i + 1) * 0.0022
            await strategy.on_tick(bot_id, {"mark": price * bump, "realized_delta": 0.0})
            await asyncio.sleep(0.35)

        if args.micro_roundtrip:
            await _micro_roundtrip(client, symbol, price)

        await asyncio.sleep(0.5)
        try:
            await client.raw.futures_cancel_all_open_orders(symbol=symbol)
            _log.info("cancelled all open orders for %s", symbol)
        except Exception:
            _log.exception("cancel_all_open_orders failed")

        await strategy.on_stop(bot_id)
        strategy = None
        _log.info("done")
        return 0
    finally:
        if strategy is not None:
            try:
                await strategy.on_stop(bot_id)
            except Exception:
                _log.exception("on_stop cleanup")
        if client is not None:
            await client.aclose()


def main() -> None:
    p = argparse.ArgumentParser(description="Run DOGEUSDT shifting grid once (Binance Futures).")
    p.add_argument("--dry-run", action="store_true", help="Do not connect to Binance.")
    p.add_argument("--mainnet", action="store_true", help="Use mainnet (overrides testnet env default).")
    p.add_argument("--levels", type=int, default=5, help="generatorCount for the grid.")
    p.add_argument("--initial-capital-usdt", type=float, default=25.0, help="Sizing hint for grid slices.")
    p.add_argument("--bootstrap-wait-s", type=float, default=6.0, help="Wait for bootstrap limit orders.")
    p.add_argument("--tick-steps", type=int, default=5, help="Simulated mark updates for trailing RAM path.")
    p.add_argument(
        "--micro-roundtrip",
        action="store_true",
        help="After grid, place minimum MARKET BUY then MARKET SELL reduce-only (requires margin).",
    )
    args = p.parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
