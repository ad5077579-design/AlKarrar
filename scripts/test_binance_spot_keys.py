"""
Smoke-test Binance Spot API keys from `.env`.

Set ``BINANCE_ENV`` to ``mainnet``, ``testnet``, or ``demo`` (Spot Demo keys from demo.binance.com).

Examples (project root):
  python scripts/test_binance_spot_keys.py
  python scripts/test_binance_spot_keys.py --symbol DOGEUSDT
  python scripts/test_binance_spot_keys.py --env demo
  python scripts/test_binance_spot_keys.py --mainnet
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def _f(x: object) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _bootstrap() -> Path:
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.chdir(root)
    return root


def _mask_key(k: str) -> str:
    k = (k or "").strip()
    if len(k) <= 8:
        return "(قصير/مخفي)"
    return f"{k[:4]}…{k[-4:]}"


async def _run(*, api_key: str, api_secret: str, env: str, symbol: str) -> int:
    _bootstrap()
    from backend.core.binance_client import BinanceSpotClient, env_display_label
    from backend.core.binance_env import BinanceSpotEnv, spot_stream_endpoint

    client: BinanceSpotClient | None = None
    spot_env: BinanceSpotEnv = env  # type: ignore[assignment]
    try:
        client = await BinanceSpotClient.create_for_env(
            api_key=api_key,
            api_secret=api_secret,
            env=spot_env,
        )
        await client.ping()
        server_ms = await client.fetch_server_time_ms()

        klines = await client.get_klines(symbol=symbol, interval="5m", limit=2)
        kline_note = f"{len(klines)} شمعة" if isinstance(klines, list) else "شكل غير متوقع"

        t = await client.fetch_ticker(symbol)
        mark = _f(t.get("lastPrice") or t.get("price") or 0)

        bal = await client.fetch_account_balance()
        mode = env_display_label(spot_env)

        print(f"OK ({mode}) server_time_ms={server_ms}")
        print(f"  مفتاح (معاينة): {_mask_key(api_key)}")
        print(f"  klines {symbol} -> {kline_note}")
        print(f"  symbol={symbol} last={mark}")
        if spot_env != "mainnet":
            print(f"  --- رصيد Spot ({spot_env}) ---")
        print(f"  USDT wallet≈{bal.get('totalWalletBalance', 0):.4f}")
        print(f"  available≈{bal.get('availableBalance', 0):.4f}")

        try:
            lk = await client.listen_key_create()
            host, port = spot_stream_endpoint(spot_env)
            print(f"  listenKey OK → wss://{host}{port}/ws/…")
            await client.listen_key_close(lk)
        except Exception as exc:
            print(f"  listenKey WARN: {type(exc).__name__}: {exc}")
            if spot_env == "demo":
                print(
                    "  (Spot Demo قد لا يدعم userDataStream REST؛ المزامنة عبر REST poll ما زالت تعمل.)"
                )
            else:
                print(
                    "  hint: طابق BINANCE_ENV مع مصدر المفتاح "
                    "(demo=demo.binance.com، testnet=testnet.binance.vision، mainnet=binance.com)."
                )

        return 0
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "  hint: فعّل Spot Trading للمفتاح؛ "
            "BINANCE_ENV=demo لمفاتيح demo.binance.com، "
            "BINANCE_ENV=testnet لمفاتيح testnet.binance.vision.",
            file=sys.stderr,
        )
        return 1
    finally:
        if client is not None:
            await client.aclose()


def main() -> None:
    _bootstrap()
    from backend.main_engine import EngineSettings

    p = argparse.ArgumentParser(description="اختبار مفاتيح Binance Spot.")
    p.add_argument("--symbol", default="DOGEUSDT")
    p.add_argument("--env", choices=("mainnet", "testnet", "demo"), default=None)
    p.add_argument("--mainnet", action="store_true", help="استخدام الإنتاج (تجاوز BINANCE_ENV)")
    p.add_argument(
        "--testnet",
        action="store_true",
        help="استخدام testnet.binance.vision (تجاوز BINANCE_ENV)",
    )
    args = p.parse_args()

    settings = EngineSettings()
    api_key = (settings.binance_api_key or os.environ.get("BINANCE_API_KEY") or "").strip()
    api_secret = (settings.binance_api_secret or os.environ.get("BINANCE_API_SECRET") or "").strip()
    if not api_key or not api_secret:
        print("أضف BINANCE_API_KEY و BINANCE_API_SECRET في .env", file=sys.stderr)
        raise SystemExit(2)

    if args.mainnet:
        env = "mainnet"
    elif args.testnet:
        env = "testnet"
    elif args.env:
        env = args.env
    else:
        env = settings.resolved_binance_env()

    print(f"BINANCE_ENV -> {env}")
    raise SystemExit(asyncio.run(_run(api_key=api_key, api_secret=api_secret, env=env, symbol=args.symbol)))


if __name__ == "__main__":
    main()
