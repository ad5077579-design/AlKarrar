"""Quick system health check. Usage: python scripts/health_check.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

API = "http://127.0.0.1:8090"
BOT = "default"
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    issues: list[str] = []
    print("=== AlKarrar health ===\n")
    try:
        r = requests.get(f"{API}/api/bots/{BOT}/dashboard", timeout=10)
        r.raise_for_status()
        d = r.json()
    except Exception as exc:
        print(f"API: DOWN ({exc})")
        return 1

    print(f"API: OK  env={d.get('binanceEnv')}  sync={d.get('syncError') or 'ok'}")
    print(f"Wallet: {float(d.get('totalWalletBalance') or 0):.2f} USDT  avail={float(d.get('availableBalance') or 0):.2f}")
    print(f"Focus: {d.get('symbol')}  mark={d.get('markPrice')}")
    lo, hi = d.get("generatorLower"), d.get("generatorUpper")
    mk = float(d.get("markPrice") or 0)
    if lo and hi and mk > 0:
        mid = (float(lo) + float(hi)) / 2
        dev = abs(mid - mk) / mk * 100
        print(f"Band: {lo} - {hi}  mid-dev={dev:.1f}%")
        if dev > 35:
            issues.append(f"band mismatch on {d.get('symbol')} ({dev:.0f}%)")

    st = requests.get(f"{API}/api/bots/{BOT}/grid/status", timeout=10).json()
    active = st.get("activeSymbols") or []
    print(f"\nGrids active: {len(active)} -> {active or '(none)'}")
    for sym in active:
        g = st.get("grids", {}).get(sym, {})
        err = str(g.get("lastError") or "").strip()
        alloc = float(g.get("allocatedCapital") or 0)
        n = int(g.get("generatorCount") or 1)
        per = alloc / max(n, 1)
        print(
            f"  {sym}: fills={g.get('virtualExecutions')} armed={g.get('ordersPlaced')} "
            f"alloc={alloc:.0f} ~{per:.1f}/line unreal={g.get('unrealizedPnlUsdt')}"
        )
        if err:
            issues.append(f"{sym}: {err[:120]}")
            print(f"    ERROR: {err[:200]}")
        if per < 11 and alloc > 0:
            issues.append(f"{sym}: per-line {per:.1f} USDT < 11 min")

    try:
        fe = requests.get("http://127.0.0.1:3000/", timeout=5)
        print(f"\nFrontend: {fe.status_code}")
    except Exception:
        issues.append("frontend not reachable on :3000")
        print("\nFrontend: DOWN")

    print("\n--- Issues ---")
    if not issues:
        print("None critical from automated checks.")
        return 0
    for i, x in enumerate(issues, 1):
        print(f"{i}. {x}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
