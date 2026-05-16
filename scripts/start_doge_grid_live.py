"""

Start DOGEUSDT shifting grid in-process (no HTTP). Requires .env keys.



  python scripts/start_doge_grid_live.py

  python scripts/start_doge_grid_live.py --stop

"""



from __future__ import annotations



import argparse

import asyncio

import json

import sys

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))





async def _run(args: argparse.Namespace) -> int:

    from backend.api.grid_manager import grid_manager
    from backend.api.grid_runner import calibrated_doge_grid_settings

    from backend.api.credential_resolver import get_binance_keys

    from backend.core.binance_client import BinanceSpotClient



    bot_id = "default"

    if args.stop:

        print(json.dumps(await grid_manager.stop("DOGEUSDT"), indent=2))

        return 0



    k1, k2, env, _legacy = await get_binance_keys(bot_id)

    if not k1 or not k2:

        print("Missing BINANCE_API_KEY / BINANCE_API_SECRET in .env", file=sys.stderr)

        return 2



    client = await BinanceSpotClient.create_for_env(

        api_key=k1, api_secret=k2, env=env

    )

    try:

        tick = await client.fetch_ticker("DOGEUSDT")

        mark = float(tick.get("price") or tick.get("lastPrice") or 0)

    finally:

        await client.aclose()



    settings = calibrated_doge_grid_settings(mark, levels=args.levels, capital_usdt=args.capital)
    settings["symbol"] = "DOGEUSDT"

    out = await grid_manager.start(bot_id, settings)

    print(json.dumps(out, indent=2, default=str))

    print("\nGrid is running. Keep API + mark feed alive, or run with dashboard WS.")

    print("Stop: python scripts/start_doge_grid_live.py --stop")

    return 0





def main() -> None:

    p = argparse.ArgumentParser()

    p.add_argument("--stop", action="store_true")

    p.add_argument("--levels", type=int, default=8)

    p.add_argument("--capital", type=float, default=40.0)

    args = p.parse_args()

    raise SystemExit(asyncio.run(_run(args)))





if __name__ == "__main__":

    main()

