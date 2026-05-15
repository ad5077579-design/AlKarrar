"""
Smoke-test اتصال Binance USD-M Futures باستخدام المفاتيح من `.env`.

- **ورق / ديمو (افتراضي مع ``BINANCE_TESTNET=true``):** منصة Binance الموحّدة للديمو
  (مفاتيح من demo.binance.com) → ``demo-fapi.binance.com`` يدوياً (بدون ``AsyncClient(demo=True)`` حتى لا يُستدعى spot).
- **إنتاج:** ``BINANCE_TESTNET=false`` أو تمرير ``--mainnet``.
- **تست نت Futures القديم فقط:** ``BINANCE_LEGACY_FUTURES_TESTNET=true`` أو ``--legacy-futures-testnet``.

أمثلة (من جذر المشروع):
  python scripts/test_binance_futures_keys.py
  python scripts/test_binance_futures_keys.py --symbol BTCUSDT
  python scripts/test_binance_futures_keys.py --mainnet
  python scripts/test_binance_futures_keys.py --legacy-futures-testnet
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


def _usdt_asset_row(acc: dict) -> dict | None:
    assets = acc.get("assets")
    if not isinstance(assets, list):
        return None
    for row in assets:
        if isinstance(row, dict) and str(row.get("asset", "")).upper() == "USDT":
            return row
    return None


async def _run(*, api_key: str, api_secret: str, paper: bool, legacy: bool, symbol: str) -> int:
    _bootstrap()
    from backend.core.binance_client import BinanceFuturesClient, parse_usdm_futures_balances

    client: BinanceFuturesClient | None = None
    try:
        client = await BinanceFuturesClient.create_for_paper_or_mainnet(
            api_key=api_key,
            api_secret=api_secret,
            paper=paper,
            legacy_futures_testnet=legacy,
        )
        # USD-M only — never spot ping / ``demo-api`` during client build.
        await client.raw.futures_ping()
        server_ms = await client.fetch_server_time_ms()

        klines = await client.futures_klines(symbol=symbol, interval="5m", limit=2)
        kline_note = f"{len(klines)} شمعة" if isinstance(klines, list) else "شكل غير متوقع"

        t = await client.fetch_ticker(symbol)
        mark = _f(t.get("lastPrice") or t.get("price") or t.get("close") or 0)

        acc = await client.fetch_account()
        bal = parse_usdm_futures_balances(acc)
        usdt = _usdt_asset_row(acc)

        if paper and legacy:
            mode = "legacy USD-M testnet (testnet.binancefuture.com)"
        elif paper:
            mode = "unified demo (demo-fapi.binance.com)"
        else:
            mode = "mainnet (fapi.binance.com)"

        print(f"OK ({mode}) server_time_ms={server_ms}")
        print(f"  مفتاح (معاينة): {_mask_key(api_key)}")
        print(f"  futures_klines {symbol} -> {kline_note}")
        print(f"  symbol={symbol} mark/last={mark}")

        if paper:
            print("  --- رصيد ورقي / ديمو (ليست أموالاً حقيقية) ---")
        else:
            print("  --- رصيد المحفظة (Mainnet) — أموال حقيقية ---")
        tw = _f(acc.get("totalWalletBalance"))
        tm = _f(acc.get("totalMarginBalance"))
        av = _f(acc.get("availableBalance"))
        up = _f(acc.get("totalUnrealizedProfit"))
        print(f"  totalWalletBalance  (إجمالي المحفظة)     : {tw}")
        print(f"  totalMarginBalance  (هامش الحساب)       : {tm}")
        print(f"  availableBalance    (متاح للتداول)     : {av}")
        print(f"  totalUnrealizedProfit (ربح/خسارة عائم) : {up}")
        if usdt:
            wb = _f(usdt.get("walletBalance"))
            cw = _f(usdt.get("crossWalletBalance"))
            ua = _f(usdt.get("availableBalance"))
            print("  --- تفصيل أصل USDT (من assets) ---")
            print(f"  USDT walletBalance      : {wb}")
            print(f"  USDT crossWalletBalance : {cw}")
            print(f"  USDT availableBalance   : {ua}")
        print("  --- نفس القيم بعد التطبيع (مثل لوحة التحكم) ---")
        print(f"  currentCapital (محفظة)  -> {bal['currentCapital']}")
        print(f"  marginBalance           -> {bal['marginBalance']}")
        print(f"  availableBalance        -> {bal['availableBalance']}")
        print(f"  floatingPnl             -> {bal['floatingPnl']}")

        try:
            lk = await client.futures_listen_key_create()
            uhost = "demo-fstream.binance.com" if (paper and not legacy) else (
                "fstream.binancefuture.com" if (paper and legacy) else "fstream.binance.com"
            )
            print(f"  userStream URL host -> {uhost} (preview listenKey {lk[:8]}…)")
        except Exception as le:
            print(f"  userStream listenKey: فشل (اختياري للاختبار): {le}")

        if (
            bal["currentCapital"] == 0.0
            and bal["availableBalance"] == 0.0
            and bal["floatingPnl"] == 0.0
        ):
            print(
                "  تنبيه: أرصدة صفر — على الديمو الموحّد خصّص أصولاً من واجهة demo.binance.com "
                "أو تأكد أن المفتاح من نفس البيئة (--legacy-futures-testnet إن كان مفتاح تست نت القديم)."
            )
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}")
        print(
            "  hint: مفاتيح demo.binance.com تحتاج وضع ورقي بدون --mainnet وبدون --legacy-futures-testnet؛ "
            "مفاتيح testnet.binancefuture.com تحتاج --legacy-futures-testnet؛ "
            "فعّل صلاحية Futures للمفتاح؛ شغّل السكربت من جذر المشروع لقراءة `.env`."
            "\n  إن ظهر Could not contact DNS servers: غالباً كان aiodns يُستخدم افتراضياً — حدّث الكود ليستخدم "
            "DNS النظام (ThreadedResolver). إن استمر الخطأ فالشبكة/DNS على الجهاز لا يصلان لـ Binance "
            "(جرّب 8.8.8.8 أو شبكة أخرى)."
        )
        return 1
    finally:
        if client is not None:
            await client.aclose()


def main() -> None:
    _bootstrap()
    from backend.main_engine import EngineSettings

    settings = EngineSettings()

    p = argparse.ArgumentParser(
        description="اختبار Binance USD-M Futures ضد الإنتاج أو الديمو الموحّد أو تست نت Futures القديم.",
    )
    p.add_argument(
        "--api-key",
        default="",
        help="إن وُجد يتجاوز .env؛ وإلا BINANCE_API_KEY",
    )
    p.add_argument(
        "--secret",
        default="",
        help="إن وُجد يتجاوز .env؛ وإلا BINANCE_API_SECRET",
    )
    p.add_argument("--symbol", default=os.getenv("ALKARRAR_SYMBOL", "DOGEUSDT"), help="رمز USD-M")
    net = p.add_mutually_exclusive_group()
    net.add_argument("--mainnet", action="store_true", help="إجبار الإنتاج (تجاهل BINANCE_TESTNET)")
    net.add_argument("--paper", action="store_true", help="إجبار وضع الورق/الديمو (مثل BINANCE_TESTNET=true)")
    p.add_argument(
        "--legacy-futures-testnet",
        action="store_true",
        help="استخدام testnet.binancefuture.com بدل demo-fapi (مع وضع الورق)",
    )
    args = p.parse_args()

    key = (args.api_key or settings.binance_api_key or "").strip()
    sec = (args.secret or settings.binance_api_secret or "").strip()
    if not key or not sec:
        p.error(
            "لا يوجد مفتاح/سر: عبّئ BINANCE_API_KEY و BINANCE_API_SECRET في `.env` "
            "أو مرّر --api-key و --secret"
        )

    if args.mainnet:
        paper = False
    elif args.paper:
        paper = True
    else:
        paper = bool(settings.binance_testnet)

    legacy = bool(settings.binance_legacy_futures_testnet) or bool(args.legacy_futures_testnet)

    sym = str(args.symbol or "DOGEUSDT").upper().replace("/", "")
    src = (
        "CLI --mainnet"
        if args.mainnet
        else "CLI --paper"
        if args.paper
        else ".env BINANCE_TESTNET"
    )
    leg = "CLI --legacy-futures-testnet" if args.legacy_futures_testnet else ".env BINANCE_LEGACY_FUTURES_TESTNET"
    print(f"مصدر الورق: {src} -> paper={paper}")
    print(f"مصدر التست نت القديم: {leg} -> legacy_futures_testnet={legacy}")
    raise SystemExit(asyncio.run(_run(api_key=key, api_secret=sec, paper=paper, legacy=legacy, symbol=sym)))


if __name__ == "__main__":
    main()
