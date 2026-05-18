"""
اكتشاف بيئة Binance Spot تلقائياً من مفاتيح .env (demo / mainnet / testnet).

  python scripts/probe_binance_env.py
  python scripts/probe_binance_env.py --no-cache
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def _bootstrap() -> Path:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.chdir(root)
    return root


async def _main(*, no_cache: bool) -> int:
    _bootstrap()
    from backend.core.binance_env import env_display_label
    from backend.core.binance_key_probe import (
        credentials_fingerprint,
        load_last_detection,
        probe_binance_env,
        reset_binance_env_probe_cache,
        resolve_binance_env_for_keys,
    )
    from backend.main_engine import EngineSettings

    settings = EngineSettings()
    key = (settings.binance_api_key or "").strip()
    secret = (settings.binance_api_secret or "").strip()
    if not key or not secret:
        print("FAIL: set BINANCE_API_KEY and BINANCE_API_SECRET in .env", file=sys.stderr)
        return 2

    hint = settings.resolved_binance_env()
    fp = credentials_fingerprint(key, secret)
    print(f"key fingerprint: {fp[:8]}...")
    print(f".env BINANCE_ENV hint: {hint}")

    if no_cache:
        reset_binance_env_probe_cache()

    detected = await resolve_binance_env_for_keys(key, secret, hint=hint)
    print(f"detected env: {detected} ({env_display_label(detected)})")

    if detected != hint:
        print("WARN: .env hint differs from detected env — engine uses detected.")

    last = load_last_detection()
    if last:
        print(f"last saved: env={last.get('binanceEnv')} at_ms={last.get('detectedAtMs')}")

    direct = await probe_binance_env(key, secret, hint=hint)
    print(f"direct probe: {direct}")
    bal_client = None
    try:
        from backend.core.binance_client import BinanceSpotClient

        bal_client = await BinanceSpotClient.create_for_env(
            api_key=key, api_secret=secret, env=detected
        )
        bal = await bal_client.fetch_account_balance()
        print(
            f"USDT balance: wallet={bal.get('totalWalletBalance', 0):.4f} "
            f"available={bal.get('availableBalance', 0):.4f}"
        )
    finally:
        if bal_client is not None:
            await bal_client.aclose()

    return 0 if detected == direct else 1


def main() -> None:
    p = argparse.ArgumentParser(description="اكتشاف بيئة Binance من المفاتيح")
    p.add_argument("--no-cache", action="store_true", help="تجاهل كاش الاكتشاف")
    args = p.parse_args()
    raise SystemExit(asyncio.run(_main(no_cache=args.no_cache)))


if __name__ == "__main__":
    main()
