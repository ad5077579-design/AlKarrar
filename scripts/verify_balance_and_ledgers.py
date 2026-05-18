"""
Verify: (1) platform USDT vs dashboard, (2) two grids, (3) backend ledger vs frontend merge logic.
Usage: python scripts/verify_balance_and_ledgers.py [--minutes 2.5]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = "http://127.0.0.1:8090"
BOT = "default"
ROOT = Path(__file__).resolve().parents[1]


def http_json(method: str, path: str, body: dict | None = None, timeout: float = 60.0) -> Any:
    url = f"{API}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ledger_key(row: dict) -> str:
    extra = row.get("extra") or {}
    oid = extra.get("orderId")
    if oid is not None and str(oid):
        return f"{row.get('actionType')}:{oid}"
    return str(row.get("id", ""))


def frontend_merge(entries: list[dict]) -> list[dict]:
    by: dict[str, dict] = {}
    for row in entries:
        by[ledger_key(row)] = row
    return sorted(by.values(), key=lambda r: int(r.get("timestampMs") or 0))


async def platform_balance() -> dict[str, float]:
    import os

    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from backend.api.credential_resolver import get_binance_keys
    from backend.core.binance_client import BinanceSpotClient

    k1, k2, env, _ = await get_binance_keys(BOT)
    if not k1 or not k2:
        raise RuntimeError("No Binance keys")
    client = await BinanceSpotClient.create_for_env(api_key=k1, api_secret=k2, env=env)
    try:
        bal = await client.fetch_account_balance()
    finally:
        await client.aclose()
    return {
        "env": env,
        "totalWalletBalance": float(bal.get("totalWalletBalance") or 0),
        "availableBalance": float(bal.get("availableBalance") or 0),
    }


def fetch_mark(symbol: str) -> float:
    sym = symbol.upper().replace("/", "")
    dash = http_json("GET", f"/api/bots/{BOT}/dashboard?symbol={sym}")
    mk = float(dash.get("markPrice") or 0)
    if mk > 0:
        return mk
    http_json("PATCH", f"/api/bots/{BOT}/settings", {"symbol": sym})
    dash2 = http_json("GET", f"/api/bots/{BOT}/dashboard?symbol={sym}")
    mk = float(dash2.get("markPrice") or 0)
    if mk > 0:
        return mk
  # REST klines last close
    kl = http_json("GET", f"/api/bots/{BOT}/klines?symbol={sym}&interval=1m&limit=1")
    if isinstance(kl, list) and kl and isinstance(kl[-1], dict):
        return float(kl[-1].get("close") or 0)
    return 0.0


def band_from_pct(mark: float, span_pct: float) -> tuple[float, float]:
    half = mark * (span_pct / 200.0)
    return mark - half, mark + half


def stop_all() -> list[str]:
    st = http_json("GET", f"/api/bots/{BOT}/grid/status")
    syms = list(st.get("activeSymbols") or [])
    for sym in syms:
        http_json("POST", f"/api/bots/{BOT}/grid/stop", {"symbol": sym})
    return syms


def compare_ledgers(symbol: str) -> dict[str, Any]:
    led = http_json("GET", f"/api/bots/{BOT}/grid/ledger?symbol={symbol}")
    raw = led.get("entries") or []
    merged = frontend_merge(raw)
    raw_keys = [ledger_key(r) for r in raw]
    merged_keys = [ledger_key(r) for r in merged]
    dup_raw = len(raw_keys) - len(set(raw_keys))
    order_rows = [r for r in raw if str(r.get("actionType", "")).startswith("ORDER_")]
    return {
        "symbol": symbol,
        "frozen": led.get("frozen"),
        "count_raw": len(raw),
        "count_after_frontend_merge": len(merged),
        "duplicate_keys_in_backend": dup_raw,
        "order_rows": len(order_rows),
        "merged_equals_raw": len(merged) == len(raw),
        "keys_only_in_raw": sorted(set(raw_keys) - set(merged_keys))[:5],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=2.5)
    parser.add_argument("--no-start", action="store_true")
    args = parser.parse_args()

    try:
        http_json("GET", f"/api/bots/{BOT}/dashboard", timeout=5.0)
    except Exception as exc:
        print(f"API down at {API}: {exc}", file=sys.stderr)
        return 1

    print("=== 1) Balance: platform vs dashboard ===")
    plat = asyncio.run(platform_balance())
    dash = http_json("GET", f"/api/bots/{BOT}/dashboard")

    fields = ("totalWalletBalance", "availableBalance")
    tol = 0.02
    ok_bal = True
    for f in fields:
        p = float(plat[f])
        d = float(dash.get(f) or 0)
        diff = abs(p - d)
        match = diff <= tol
        ok_bal = ok_bal and match
        print(f"  {f}: platform={p:.8f}  UI/dashboard={d:.8f}  diff={diff:.8f}  {'OK' if match else 'MISMATCH'}")
    print(f"  binanceEnv: platform env={plat['env']}  dashboard={dash.get('binanceEnv')}")
    print(f"  syncError: {dash.get('syncError') or '(none)'}")

    grids = {
        "TRXUSDT": {"generatorCount": 20, "allocatedCapital": 350.0, "span_pct": 3.5},
        "XRPUSDT": {"generatorCount": 15, "allocatedCapital": 350.0, "span_pct": 3.5},
    }

    print("\n=== 2) Stop existing grids ===")
    stopped = stop_all()
    print(f"  stopped: {stopped or '(none)'}")
    time.sleep(2)

    if not args.no_start:
        print("\n=== 3) Start two grids ===")
        for sym, cfg in grids.items():
            mark = fetch_mark(sym)
            lo, hi = band_from_pct(mark, float(cfg["span_pct"]))
            body = {
                "symbol": sym,
                "calibrate": False,
                "generatorCount": cfg["generatorCount"],
                "allocatedCapital": cfg["allocatedCapital"],
                "initialCapital": cfg["allocatedCapital"],
                "generatorLower": lo,
                "generatorUpper": hi,
                "trailingOffset": max(mark * 0.002, 0.001),
            }
            print(f"  {sym} mark={mark:.6f} band=[{lo:.6f},{hi:.6f}] alloc={cfg['allocatedCapital']}")
            try:
                out = http_json("POST", f"/api/bots/{BOT}/grid/start", body)
                per = cfg["allocatedCapital"] / cfg["generatorCount"]
                print(f"    -> running={out.get('running')} per_line~{per:.2f} USDT")
            except urllib.error.HTTPError as e:
                err = e.read().decode("utf-8", errors="replace")
                print(f"    -> FAIL {e.code}: {err[:300].encode('ascii', 'replace').decode()}")
                return 1

    duration = max(90.0, args.minutes * 60.0)
    interval = 25.0
    print(f"\n=== 4) Monitor {duration:.0f}s — ledger backend vs frontend merge ===\n")
    t0 = time.time()
    last_ledger: dict[str, Any] = {}
    while time.time() - t0 < duration:
        st = http_json("GET", f"/api/bots/{BOT}/grid/status")
        print(f"@{time.strftime('%H:%M:%S')} active={st.get('activeSymbols')}")
        for sym in grids:
            cmp = compare_ledgers(sym)
            last_ledger[sym] = cmp
            print(
                f"  {sym}: entries={cmp['count_raw']} merged={cmp['count_after_frontend_merge']} "
                f"ORDER rows={cmp['order_rows']} dup_keys={cmp['duplicate_keys_in_backend']} "
                f"raw==merged={cmp['merged_equals_raw']}"
            )
        acc = http_json("GET", f"/api/bots/{BOT}/dashboard")
        print(
            f"  wallet={float(acc.get('totalWalletBalance') or 0):.4f} "
            f"avail={float(acc.get('availableBalance') or 0):.4f}"
        )
        print("-" * 50)
        time.sleep(interval)

    report = {
        "balance_ok": ok_bal,
        "platform": plat,
        "dashboard_final": {f: dash.get(f) for f in fields},
        "ledger": last_ledger,
        "active_at_end": http_json("GET", f"/api/bots/{BOT}/grid/status").get("activeSymbols"),
    }
    out_path = ROOT / "data" / "verify_session_report.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {out_path}")

    ledger_ok = all(
        v.get("merged_equals_raw") and v.get("duplicate_keys_in_backend", 0) == 0
        for v in last_ledger.values()
    )
    if ok_bal and ledger_ok:
        print("\nPASS: balance match + ledger consistent (backend == frontend merge)")
        return 0
    if ok_bal:
        print("\nPARTIAL: balance OK; check ledger duplicates or counts above")
        return 0
    print("\nFAIL: balance mismatch")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
