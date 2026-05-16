"""

DOGEUSDT grid session: calibrate band, place orders, poll fills/PnL, then stop.



  python scripts/run_doge_grid_session.py

  python scripts/run_doge_grid_session.py --hold-seconds 90

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

    from backend.api.credential_resolver import get_binance_keys

    from backend.api.grid_manager import grid_manager
    from backend.api.grid_runner import calibrated_doge_grid_settings

    from backend.api.trade_journal import sync_trades_from_exchange

    from backend.core.binance_client import BinanceSpotClient

    from backend.database import async_session_factory, init_db



    await init_db()

    bot_id = "default"

    k1, k2, env, _legacy = await get_binance_keys(bot_id)

    if not k1 or not k2:

        print("Missing keys in .env", file=sys.stderr)

        return 2



    client = await BinanceSpotClient.create_for_env(

        api_key=k1, api_secret=k2, env=env

    )

    sym = "DOGEUSDT"

    try:

        tick = await client.fetch_ticker(sym)

        mark = float(tick.get("price") or tick.get("lastPrice") or 0)

        settings = calibrated_doge_grid_settings(

            mark, levels=args.levels, capital_usdt=args.capital

        )

        print("=== calibrated settings ===")

        print(json.dumps(settings, indent=2))



        settings["symbol"] = sym
        started = await grid_manager.start(bot_id, settings)

        print("\n=== grid started ===")

        print(json.dumps(started, indent=2, default=str))



        await asyncio.sleep(float(args.bootstrap_wait))

        print(f"\n=== after bootstrap ({args.bootstrap_wait}s) ===")

        r_doge = grid_manager.get_runner(sym)
        print("ordersPlaced:", (r_doge.status().get("ordersPlaced") if r_doge else 0))



        open_orders = await client.get_open_orders(sym)

        print("open_orders:", len(open_orders))



        for _ in range(int(args.poll_ticks)):

            tick = await client.fetch_ticker(sym)

            m = float(tick.get("price") or mark)

            r = grid_manager.get_runner(sym)
            if r:
                await r.on_mark(m)

            await asyncio.sleep(float(args.poll_interval))



        async with async_session_factory() as db:

            trades = await sync_trades_from_exchange(

                client, db, bot_id=bot_id, symbol=sym, limit=50

            )

        print("\n=== trades from exchange ===")

        print("count:", len(trades))

        if trades:

            print(json.dumps(trades[:5], indent=2))

            total_comm = sum(float(t.get("commission") or 0) for t in trades)

            print(f"sum commission: {total_comm:.6f}")



        bal = await client.fetch_account_balance()

        print("\n=== account ===")

        print(json.dumps(bal, indent=2))



    finally:

        await grid_manager.stop(sym)

        await client.aclose()

        print("\n=== grid stopped, orders cancelled ===")

    return 0





def main() -> None:

    p = argparse.ArgumentParser()

    p.add_argument("--levels", type=int, default=8)

    p.add_argument("--capital", type=float, default=40.0)

    p.add_argument("--bootstrap-wait", type=float, default=14.0)

    p.add_argument("--poll-ticks", type=int, default=8)

    p.add_argument("--poll-interval", type=float, default=4.0)

    args = p.parse_args()

    raise SystemExit(asyncio.run(_run(args)))





if __name__ == "__main__":

    main()

