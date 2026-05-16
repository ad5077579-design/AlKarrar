"""
Calibrate and start DOGEUSDT shifting grid via running API (demo keys in .env).

  python scripts/calibrate_doge_grid.py
  python scripts/calibrate_doge_grid.py --stop
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _post(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8090")
    p.add_argument("--bot-id", default="default")
    p.add_argument("--stop", action="store_true")
    p.add_argument("--levels", type=int, default=8)
    p.add_argument("--capital", type=float, default=40.0)
    args = p.parse_args()
    base = args.api.rstrip("/")
    bid = args.bot_id

    try:
        if args.stop:
            out = _post(f"{base}/api/bots/{bid}/grid/stop")
            print(json.dumps(out, indent=2))
            return 0
        out = _post(
            f"{base}/api/bots/{bid}/grid/start",
            {
                "calibrate": True,
                "symbol": "DOGEUSDT",
                "levels": args.levels,
                "initialCapital": args.capital,
            },
        )
        print(json.dumps(out, indent=2))
        trades = _get(f"{base}/api/bots/{bid}/trades?symbol=DOGEUSDT&limit=20&sync=true")
        print("\n--- trades summary ---")
        print(json.dumps(trades.get("summary"), indent=2))
        return 0
    except urllib.error.HTTPError as e:
        print(e.read().decode(), file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"API not reachable: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
