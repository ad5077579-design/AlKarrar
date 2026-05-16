"""
تفريغ حساب Binance Spot: إلغاء **كل** الأوامر المعلّقة، ثم بيع **كل** الأصول
القابلة للتداول مقابل USDT بأوامر سوق (MARKET).

الاعتماد على مفاتيح ``.env`` و ``BINANCE_ENV`` (مثل ``scripts/test_binance_spot_keys.py``).

معاينة فقط (افتراضي — لا تغيير على المنصة):
  python scripts/spot_flatten_and_clean.py

تنفيذ فعلي:
  python scripts/spot_flatten_and_clean.py --execute

زوج واحد فقط (مثل إيقاف طارئ لرمز محدد):
  python scripts/spot_flatten_and_clean.py --symbol DOGEUSDT --execute

خيارات:
  --cancel-only   إلغاء الأوامر فقط دون بيع الأرصدة.
  --dry-run       صريح: نفس السلوك الافتراضي (معاينة).

تحذير: على mainnet يُباع فعلياً بالسوق. لا تلصق المفاتيح في الطرفية ولا في Git.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from pathlib import Path
from typing import Any

from binance.exceptions import BinanceAPIException

from backend.core.spot_market_filters import NON_GRID_STABLE_BASE_ASSETS

# أصول نقية / عملات ورقية — لا نلمسها كـ «مراكز للبيع»
_SKIP_BASE_ASSETS: frozenset[str] = NON_GRID_STABLE_BASE_ASSETS


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _bootstrap() -> None:
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.chdir(root)


def _clamp_market_sell_qty(
    free_base: float,
    price: float,
    *,
    tick_size: float,
    step_size: float,
    min_qty: float,
    min_notional: float,
) -> tuple[float | None, str]:
    from backend.core.exchange_filters import quantize_price

    p = quantize_price(float(price), float(tick_size or 0))
    if p <= 0:
        return None, "invalid_price"
    if step_size and step_size > 0:
        q = math.floor(free_base / step_size + 1e-12) * step_size
    else:
        q = free_base
    q = float(f"{q:.14f}")  # avoid float drift
    if q < float(min_qty) - 1e-12:
        return None, "below_min_qty"
    if q * p < float(min_notional) * 0.998:
        return None, "below_min_notional_dust"
    return q, ""


async def _run(
    *,
    execute: bool,
    cancel_only: bool,
    symbol_filter: str | None,
    api_key: str,
    api_secret: str,
    env: Any,
) -> int:
    from backend.core.binance_client import BinanceSpotClient
    from backend.core.exchange_filters import format_decimal, parse_symbol_filters

    client: BinanceSpotClient | None = None
    try:
        client = await BinanceSpotClient.create_for_env(
            api_key=api_key,
            api_secret=api_secret,
            env=env,
        )
        raw = client._raw

        # --- 1) فتح الأوامر ---
        if symbol_filter:
            sym = symbol_filter.upper().replace("/", "")
            open_list = await client.get_open_orders(sym)
            symbols_to_cancel = {sym} if open_list else set()
            print(f"أوامر مفتوحة لـ {sym}: {len(open_list)}")
        else:
            open_list = await client.get_all_open_orders()
            symbols_to_cancel = {str(o.get("symbol", "")).upper() for o in open_list}
            symbols_to_cancel.discard("")
            print(f"إجمالي أوامر مفتوحة: {len(open_list)} عبر {len(symbols_to_cancel)} زوج")

        if not execute:
            for s in sorted(symbols_to_cancel):
                n = sum(1 for o in open_list if str(o.get("symbol", "")).upper() == s)
                print(f"  [معاينة] إلغاء الكل: {s} ({n} أمر)")
        else:
            for s in sorted(symbols_to_cancel):
                await client.cancel_all_open_orders(symbol=s)
                n = sum(1 for o in open_list if str(o.get("symbol", "")).upper() == s)
                print(f"  تم إلغاء أوامر الزوج: {s} ({n} أمر)")

        if cancel_only:
            print("تم تخطي بيع الأرصدة (--cancel-only).")
            return 0

        # --- 2) خريطة أصول -> رمز USDT ---
        info = await client.get_exchange_info()
        base_to_symbol: dict[str, str] = {}
        symbol_row: dict[str, dict[str, Any]] = {}
        for row in info.get("symbols") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("quoteAsset", "")).upper() != "USDT":
                continue
            if str(row.get("status", "")).upper() != "TRADING":
                continue
            base = str(row.get("baseAsset", "")).upper()
            sym = str(row.get("symbol", "")).upper()
            if base and sym:
                base_to_symbol.setdefault(base, sym)
                symbol_row[sym] = row

        acc = await client.fetch_account()
        balances = acc.get("balances") or []

        sell_tasks: list[tuple[str, str, float, str]] = []  # symbol, asset, free, note

        for row in balances:
            if not isinstance(row, dict):
                continue
            asset = str(row.get("asset", "")).upper()
            if not asset or asset in _SKIP_BASE_ASSETS:
                continue
            try:
                free = float(row.get("free") or 0)
            except (TypeError, ValueError):
                continue
            if free <= 0:
                continue

            if symbol_filter:
                sf = symbol_filter.upper().replace("/", "")
                want_base = sf[:-4] if sf.endswith("USDT") else sf
                if asset != want_base:
                    continue
                sym = sf if sf in symbol_row else base_to_symbol.get(asset)
            else:
                sym = base_to_symbol.get(asset)

            if not sym or sym not in symbol_row:
                note = "لا يوجد زوج USDT تداول لهذا الأصل"
                sell_tasks.append(("", asset, free, note))
                continue

            sell_tasks.append((sym, asset, free, ""))

        print("\nأرصدة للبيع (تقريباً بالـ free بعد إلغاء الأوامر):" if execute else "\n[معاينة] أرصدة مرشّحة للبيع:")
        for sym, asset, free, note in sorted(sell_tasks, key=lambda x: x[1]):
            if note:
                print(f"  {asset}: free={free:.8f} — {note}")
                continue
            print(f"  {asset} -> {sym} free={free:.8f}")

        if not execute:
            print("\nلم يُنفَّذ شيء. أضف --execute لتطبيق الإلغاء والبيع.")
            return 0

        # إعادة جلب الحساب بعد الإلغاء؛ ثم قبل كل عملية بيع أيضاً لتجنب −2010
        for sym, asset, _old_free, note in sorted(sell_tasks, key=lambda x: x[1]):
            if note or not sym:
                continue
            row_info = symbol_row.get(sym)
            if not row_info:
                continue
            acc_snap = await client.fetch_account()
            free_base = 0.0
            for brow in acc_snap.get("balances") or []:
                if not isinstance(brow, dict):
                    continue
                if str(brow.get("asset", "")).upper() != asset:
                    continue
                try:
                    free_base = float(brow.get("free") or 0)
                except (TypeError, ValueError):
                    free_base = 0.0
                break
            if free_base <= 0:
                print(f"  تخطي {sym}: رصيد حر صفر أو منصرف بالكامل")
                continue
            filters = parse_symbol_filters(row_info)
            t = await client.fetch_ticker(sym)
            price = float(t.get("price") or t.get("lastPrice") or 0)
            q_ok, reason = _clamp_market_sell_qty(
                free_base,
                price,
                tick_size=filters["tick_size"],
                step_size=filters["step_size"],
                min_qty=filters["min_qty"],
                min_notional=filters["min_notional"],
            )
            if q_ok is None:
                print(f"  تخطي {sym}: {reason} (free={free_base:.8f} price={price})")
                continue
            qty_str = format_decimal(q_ok, filters["step_size"])
            print(f"  بيع سوق {sym} qty={qty_str} (free≈{free_base:.8f})")
            try:
                await client.create_order(
                    symbol=sym,
                    side="SELL",
                    order_type="MARKET",
                    quantity=qty_str,
                )
            except BinanceAPIException as exc:
                print(f"  فشل {sym}: Binance [{exc.code}] {exc}")
                continue

        print("\nانتهى. راجع محفظة Spot على المنصة.")
        return 0
    finally:
        if client is not None:
            await client.aclose()


def main() -> None:
    _bootstrap()

    parser = argparse.ArgumentParser(description="إلغاء أوامر Spot وبيع الأصول مقابل USDT")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="تنفيذ فعلي؛ بدونها معاينة فقط",
    )
    parser.add_argument(
        "--cancel-only",
        action="store_true",
        help="إلغاء الأوامر فقط دون بيع",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="",
        help="تقييد الإلغاء/البيع على زوج واحد (مثال: DOGEUSDT)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="صريح: معاينة (هو الافتراضي)",
    )
    parser.add_argument(
        "--env",
        type=str,
        default="",
        help="تجاوز BINANCE_ENV: mainnet | testnet | demo",
    )
    args = parser.parse_args()

    from backend.core.binance_env import BinanceSpotEnv, normalize_binance_env
    from backend.main_engine import EngineSettings

    settings = EngineSettings()
    raw_env = (args.env or settings.binance_env or "").strip()
    env: BinanceSpotEnv = normalize_binance_env(
        raw_env if raw_env else None,
        testnet_fallback=bool(settings.binance_testnet),
    )
    api_key = (settings.binance_api_key or "").strip()
    api_secret = (settings.binance_api_secret or "").strip()
    if not api_key or not api_secret:
        print("خطأ: عرّف BINANCE_API_KEY و BINANCE_API_SECRET في .env", file=sys.stderr)
        sys.exit(2)

    execute = bool(args.execute)
    if args.dry_run:
        execute = False

    sym_f = args.symbol.strip().upper().replace("/", "") or None

    print(f"البيئة: {env}  |  تنفيذ: {'نعم' if execute else 'لا (معاينة)'}")
    if sym_f:
        print(f"نطاق الزوج: {sym_f}")
    if args.cancel_only:
        print("وضع: إلغاء أوامر فقط")

    code = asyncio.run(
        _run(
            execute=execute,
            cancel_only=bool(args.cancel_only),
            symbol_filter=sym_f,
            api_key=api_key,
            api_secret=api_secret,
            env=env,
        )
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
