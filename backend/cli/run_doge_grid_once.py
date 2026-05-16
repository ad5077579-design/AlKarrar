"""
Run AlKarrar_Pro_Shifting_Grid once for DOGEUSDT (Binance Spot), then clean up.

Examples:
  python -m backend.cli.run_doge_grid_once --dry-run
  python -m backend.cli.run_doge_grid_once
  python -m backend.cli.run_doge_grid_once --micro-roundtrip

Requires ``.env`` with ``BINANCE_API_KEY`` / ``BINANCE_API_SECRET`` unless ``--dry-run``.
Default: ``BINANCE_TESTNET=true`` (Spot Testnet — keys from testnet.binance.vision).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from backend.core.exchange_filters import fetch_symbol_filters, normalize_order

_log = logging.getLogger("run_doge_grid_once")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )


async def _micro_roundtrip(client: Any, symbol: str, price: float, filters: dict[str, float]) -> None:
    from backend.core.exchange_filters import min_trade_qty

    qty = min_trade_qty(price, filters)
    qty_s = normalize_order(price, qty, filters)[1]
    _log.info("micro_roundtrip qty=%s", qty_s)
    await client.create_order(
        symbol=symbol,
        side="BUY",
        order_type="MARKET",
        quantity=qty_s,
    )
    await asyncio.sleep(0.75)
    await client.create_order(
        symbol=symbol,
        side="SELL",
        order_type="MARKET",
        quantity=qty_s,
    )


async def _run(args: argparse.Namespace) -> int:
    _setup_logging()
    symbol = "DOGEUSDT"
    bot_id = "doge-grid-once"

    if args.dry_run:
        _log.info("dry-run: would trade %s on spot testnet=%s", symbol, not args.mainnet)
        return 0

    from backend.core.binance_client import BinanceSpotClient
    from backend.main_engine import EngineSettings
    from backend.strategies.alkarrar_pro_shifting_grid import AlKarrarProShiftingGridStrategy

    settings = EngineSettings()
    if not (settings.binance_api_key and settings.binance_api_secret):
        _log.error("Missing BINANCE_API_KEY / BINANCE_API_SECRET in environment or .env")
        return 2

    if args.mainnet:
        spot_env = "mainnet"
    else:
        spot_env = settings.resolved_binance_env()
    client: Any = None
    strategy: Any = None
    try:
        client = await BinanceSpotClient.create_for_env(
            api_key=settings.binance_api_key,
            api_secret=settings.binance_api_secret,
            env=spot_env,
        )
        filters = await fetch_symbol_filters(client, symbol)
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
        strategy._quantize_hooks = lambda p, q: normalize_order(p, q, filters)  # type: ignore[attr-defined]
        await strategy.on_start(bot_id, strat_settings)
        _log.info("strategy started mark≈%s band [%.6f, %.6f]", price, lower, upper)

        await asyncio.sleep(float(args.bootstrap_wait_s))

        for i in range(int(args.tick_steps)):
            bump = 1.0 + (i + 1) * 0.0022
            await strategy.on_tick(bot_id, {"mark": price * bump, "price": price * bump})
            await asyncio.sleep(0.35)

        if args.micro_roundtrip:
            await _micro_roundtrip(client, symbol, price, filters)

        await asyncio.sleep(0.5)
        try:
            await client.cancel_all_open_orders(symbol=symbol)
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
    p = argparse.ArgumentParser(description="Run DOGEUSDT shifting grid once (Binance Spot).")
    p.add_argument("--dry-run", action="store_true", help="Do not connect to Binance.")
    p.add_argument("--mainnet", action="store_true", help="Use mainnet (overrides testnet env default).")
    p.add_argument("--levels", type=int, default=5, help="generatorCount for the grid.")
    p.add_argument("--initial-capital-usdt", type=float, default=25.0, help="Sizing hint for grid slices.")
    p.add_argument("--bootstrap-wait-s", type=float, default=6.0, help="Wait for bootstrap limit orders.")
    p.add_argument("--tick-steps", type=int, default=5, help="Simulated mark updates for trailing RAM path.")
    p.add_argument(
        "--micro-roundtrip",
        action="store_true",
        help="After grid, place minimum MARKET BUY then MARKET SELL (Spot wallet).",
    )
    args = p.parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
