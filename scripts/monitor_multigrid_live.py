"""
Live multi-grid monitor (3–5 min). Starts SOL/TRX/XRP grids on Demo API and samples ledger + risk.
Usage: python scripts/monitor_multigrid_live.py [--minutes 3] [--no-start]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

API = "http://127.0.0.1:8090"
BOT = "default"


def http_json(method: str, path: str, body: dict | None = None, timeout: float = 60.0) -> Any:
    url = f"{API}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {e.code}: {err_body[:800]}") from e


def fetch_mark(symbol: str) -> float:
    sym = symbol.upper().replace("/", "")
    dash = http_json("GET", f"/api/bots/{BOT}/dashboard?symbol={sym}")
    mk = float(dash.get("markPrice") or 0)
    if mk > 0:
        return mk
    http_json("PATCH", f"/api/bots/{BOT}/settings", {"symbol": sym})
    dash2 = http_json("GET", f"/api/bots/{BOT}/dashboard?symbol={sym}")
    return float(dash2.get("markPrice") or 0)


def band_from_pct(mark: float, span_pct: float) -> tuple[float, float]:
    half = mark * (span_pct / 200.0)
    return mark - half, mark + half


def start_grid(symbol: str, settings: dict[str, Any]) -> dict[str, Any]:
    body = {"calibrate": False, "symbol": symbol, **settings}
    return http_json("POST", f"/api/bots/{BOT}/grid/start", body)


def stop_all() -> None:
    st = http_json("GET", f"/api/bots/{BOT}/grid/status")
    for sym in st.get("activeSymbols") or []:
        try:
            http_json("POST", f"/api/bots/{BOT}/grid/stop", {"symbol": sym})
        except Exception:
            pass


def analyze_ledger(entries: list[dict]) -> dict[str, Any]:
    orders: list[dict] = []
    by_oid: dict[str, int] = defaultdict(int)
    for e in entries:
        act = str(e.get("actionType", ""))
        if act not in ("ORDER_BUY", "ORDER_SELL"):
            continue
        qty = float(e.get("quantity") or 0)
        fill = float(e.get("fillPrice") or 0)
        target = float(e.get("targetPrice") or 0)
        notional = qty * fill if fill > 0 else qty * target
        oid = str((e.get("extra") or {}).get("orderId", ""))
        if oid:
            by_oid[oid] += 1
        orders.append(
            {
                "ts": e.get("timestampMs"),
                "action": act,
                "notional": round(notional, 4),
                "qty": qty,
                "fill": fill,
                "target": target,
                "oid": oid,
            }
        )
    dup_oids = {k: v for k, v in by_oid.items() if v > 1}
    notionals = [o["notional"] for o in orders if o["notional"] > 0]
    return {
        "order_rows": len(orders),
        "unique_order_ids": len(by_oid),
        "duplicate_oid_rows": dup_oids,
        "notional_min": min(notionals) if notionals else 0,
        "notional_max": max(notionals) if notionals else 0,
        "notional_avg": round(sum(notionals) / len(notionals), 4) if notionals else 0,
        "last_orders": orders[-6:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=3.5)
    parser.add_argument("--no-start", action="store_true", help="Only monitor; do not start grids")
    parser.add_argument("--restart", action="store_true", help="Stop all grids before start")
    args = parser.parse_args()
    duration_s = max(60.0, args.minutes * 60.0)
    interval_s = 20.0

    try:
        http_json("GET", f"/api/bots/{BOT}/dashboard", timeout=5.0)
    except Exception as exc:
        print(f"API unreachable at {API}: {exc}", file=sys.stderr)
        return 1

    grids_cfg = {
        "SOLUSDT": {
            "generatorCount": 50,
            "allocatedCapital": 500.0,
            "span_pct": 0.60,
        },
        "TRXUSDT": {
            "generatorCount": 25,
            "allocatedCapital": 500.0,
            "span_pct": None,
        },
        "XRPUSDT": {
            "generatorCount": 18,
            "allocatedCapital": 500.0,
            "span_pct": None,
        },
    }

    if args.restart and not args.no_start:
        print("Stopping existing grids…")
        stop_all()
        time.sleep(2)

    if not args.no_start:
        for sym, cfg in grids_cfg.items():
            mark = fetch_mark(sym)
            if mark <= 0:
                print(f"WARN: no mark for {sym}, skip start")
                continue
            settings: dict[str, Any] = {
                "generatorCount": cfg["generatorCount"],
                "allocatedCapital": cfg["allocatedCapital"],
                "initialCapital": cfg["allocatedCapital"],
            }
            if cfg.get("span_pct"):
                lo, hi = band_from_pct(mark, float(cfg["span_pct"]))
                settings["generatorLower"] = lo
                settings["generatorUpper"] = hi
            else:
                dash = http_json("GET", f"/api/bots/{BOT}/dashboard?symbol={sym}")
                lo = float(dash.get("generatorLower") or 0)
                hi = float(dash.get("generatorUpper") or 0)
                if not (hi > lo > 0):
                    half = mark * 0.006
                    lo, hi = mark - half, mark + half
                settings["generatorLower"] = lo
                settings["generatorUpper"] = hi
            print(f"Starting {sym} mark={mark:.6f} band=[{settings['generatorLower']:.6f},{settings['generatorUpper']:.6f}] …")
            try:
                out = start_grid(sym, settings)
                print(f"  OK running={out.get('running')} alloc={out.get('allocatedCapital')}")
            except Exception as exc:
                print(f"  FAIL {sym}: {exc}")

    samples: list[dict] = []
    t0 = time.time()
    print(f"\nMonitoring {duration_s:.0f}s (every {interval_s:.0f}s)…\n")
    while time.time() - t0 < duration_s:
        tick = datetime.now(timezone.utc).isoformat()
        sample: dict[str, Any] = {"at": tick, "grids": {}, "ledger": {}, "account": {}}
        try:
            st = http_json("GET", f"/api/bots/{BOT}/grid/status")
            sample["active"] = st.get("activeSymbols")
            for sym, g in (st.get("grids") or {}).items():
                alloc = float(g.get("allocatedCapital") or 0)
                n = max(int(g.get("generatorCount") or 1), 1)
                sample["grids"][sym] = {
                    "virtualExecutions": g.get("virtualExecutions"),
                    "ordersPlaced": g.get("ordersPlaced"),
                    "deployCapitalUsdt": g.get("deployCapitalUsdt"),
                    "gridEquityUsdt": g.get("gridEquityUsdt"),
                    "unrealizedPnlUsdt": g.get("unrealizedPnlUsdt"),
                    "cumulativeRealizedUsdt": g.get("cumulativeRealizedUsdt"),
                    "expected_usdt_per_line": round(alloc / n, 4) if alloc else 0,
                    "lastError": g.get("lastError"),
                }
        except Exception as exc:
            sample["grid_status_error"] = str(exc)

        try:
            dash = http_json("GET", f"/api/bots/{BOT}/dashboard")
            sample["account"] = {
                "totalWalletBalance": dash.get("totalWalletBalance"),
                "availableBalance": dash.get("availableBalance"),
                "peakEquityUsdt": dash.get("peakEquityUsdt"),
                "currentDrawdownPct": dash.get("currentDrawdownPct"),
                "trailingEquityStopTriggered": dash.get("trailingEquityStopTriggered"),
                "trailingEquityStopEnabled": dash.get("trailingEquityStopEnabled"),
            }
        except Exception as exc:
            sample["dashboard_error"] = str(exc)

        for sym in grids_cfg:
            try:
                led = http_json("GET", f"/api/bots/{BOT}/grid/ledger?symbol={sym}")
                sample["ledger"][sym] = analyze_ledger(led.get("entries") or [])
                sample["ledger"][sym]["total_entries"] = led.get("count")
            except Exception as exc:
                sample["ledger"][sym] = {"error": str(exc)}

        samples.append(sample)
        print(json.dumps(sample, ensure_ascii=False, indent=2))
        print("-" * 60)
        time.sleep(interval_s)

    report_path = "data/multigrid_monitor_report.json"
    try:
        import os
        from pathlib import Path

        Path("data").mkdir(exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump({"duration_s": duration_s, "samples": samples}, f, ensure_ascii=False, indent=2)
        print(f"\nReport written: {report_path}")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
