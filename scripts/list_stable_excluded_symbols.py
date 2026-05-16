#!/usr/bin/env python3
"""List USDT pairs excluded from grid trading (stablecoins / fiat-like bases)."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from backend.core.binance_client import BinanceSpotClient
from backend.core.spot_market_filters import NON_GRID_STABLE_BASE_ASSETS, list_excluded_stable_usdt_pairs
from backend.main_engine import EngineSettings


async def main() -> None:
    env = EngineSettings().resolved_binance_env()
    key = os.getenv("BINANCE_API_KEY", "")
    secret = os.getenv("BINANCE_API_SECRET", "")
    client = await BinanceSpotClient.create_for_env(api_key=key, api_secret=secret, env=env)
    info = await client.get_exchange_info()
    await client.aclose()

    rows = [r for r in info.get("symbols", []) if isinstance(r, dict)]
    excluded = list_excluded_stable_usdt_pairs(rows)
    print(f"BINANCE_ENV={env}")
    print(f"Excluded USDT pairs on exchange ({len(excluded)}):")
    for sym in excluded:
        base = next(
            (str(r.get("baseAsset", "")) for r in rows if str(r.get("symbol")) == sym),
            sym.replace("USDT", ""),
        )
        print(f"  {sym}  ({base})")
    print()
    print(f"Stable base filter set ({len(NON_GRID_STABLE_BASE_ASSETS)} assets):")
    print("  " + ", ".join(sorted(NON_GRID_STABLE_BASE_ASSETS)))


if __name__ == "__main__":
    asyncio.run(main())
