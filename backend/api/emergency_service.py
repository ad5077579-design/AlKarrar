"""Programmatic emergency stop (cancel orders, flatten base, stop grids)."""



from __future__ import annotations



import logging

from datetime import datetime, timezone

from typing import Any



from backend.api.bot_hub import hub

from backend.api.credential_resolver import get_binance_keys

from backend.api.grid_manager import grid_manager

from backend.core.binance_client import BinanceSpotClient

from backend.core.exchange_filters import fetch_symbol_filters, normalize_order



_log = logging.getLogger(__name__)





async def execute_emergency_stop(bot_id: str = "default", *, symbol: str | None = None) -> dict[str, Any]:

    """

    Stop grid runner(s), cancel open Spot orders, market-sell free base.



    When ``symbol`` is set, only that pair is affected (isolated trailing equity stop).

    """

    sym_filter = symbol.strip().upper().replace("/", "") if symbol else ""

    active = grid_manager.active_symbols()

    if sym_filter:

        symbols = [sym_filter] if sym_filter in active else ([sym_filter] if sym_filter else [])

    else:

        symbols = active

        if not symbols:

            fb = str(hub.state.get("symbol") or "DOGEUSDT").upper().replace("/", "")

            symbols = [fb]



    from backend.api.grid_live_ledger import grid_live_ledger



    for sym in symbols:

        grid_live_ledger.append(

            sym,

            bot_id=bot_id,

            action_type="EMERGENCY_STOP",

            trigger_reason=(

                f"إيقاف طوارئ معزول — {sym}"

                if sym_filter

                else "إيقاف طوارئ — Trailing Equity أو أمر يدوي من الواجهة"

            ),

            mark_price=float(hub.state.get("markPrice") or 0),

        )

    if sym_filter:

        grid_live_ledger.freeze(sym_filter, reason="emergency_stop")

    else:

        grid_live_ledger.freeze_all_active(symbols, reason="emergency_stop")



    await hub.broadcast(

        {

            "type": "emergency",

            "bot_id": bot_id,

            "symbol": sym_filter or None,

            "ts": datetime.now(timezone.utc).isoformat(),

        }

    )

    try:

        if sym_filter:

            await grid_manager.stop(sym_filter, manual=False)

        else:

            await grid_manager.stop_all(manual=False)

    except Exception:

        _log.exception("emergency_stop: grid stop failed")



    key, secret, env, _legacy = await get_binance_keys(bot_id)

    if not key or not secret:

        _log.warning("emergency_stop: no API keys; broadcast + grid stop only")

        return {"status": "broadcast_only", "detail": "no API keys", "symbols": symbols}



    client: BinanceSpotClient | None = None

    try:

        client = await BinanceSpotClient.create_for_env(

            api_key=key,

            api_secret=secret,

            env=env,

        )

        for sym in symbols:

            await client.cancel_all_open_orders(symbol=sym)

            acc = await client.fetch_account()

            free_base = client.base_asset_free(acc, sym)

            if free_base > 0:

                filters = await fetch_symbol_filters(client, sym)

                mark_tick = await client.fetch_ticker(sym)

                mark = float(mark_tick.get("price") or 0)

                _, qty_s = normalize_order(mark, free_base, filters)

                if float(qty_s) > 0:

                    await client.create_order(

                        symbol=sym,

                        side="SELL",

                        order_type="MARKET",

                        quantity=qty_s,

                    )

        return {"status": "ok", "symbols": symbols, "symbol": sym_filter or None}

    except Exception as exc:

        _log.exception("emergency_stop failed")

        return {"status": "error", "detail": str(exc)[:400], "symbols": symbols}

    finally:

        if client is not None:

            await client.aclose()

