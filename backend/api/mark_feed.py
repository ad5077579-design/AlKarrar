"""Spot ticker WebSocket (combined streams) → hub per-room marks + grid dispatch."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from backend.api.bot_hub import hub
from backend.api.credential_resolver import get_binance_keys
from backend.core.binance_env import spot_stream_endpoint
from backend.main_engine import EngineSettings

_log = logging.getLogger(__name__)


def _stream_segment_for_symbol(symbol: str) -> str:
    return f"{symbol.strip().lower().replace('/', '')}@ticker"


async def run_mark_price_feed(stop: asyncio.Event) -> None:
    if os.getenv("ALKARRAR_MARK_FEED", "true").lower() not in ("1", "true", "yes"):
        return
    try:
        import websockets
    except ImportError:
        _log.warning("websockets not installed; mark feed disabled")
        return

    while not stop.is_set():
        try:
            k1, k2, env, _legacy = await get_binance_keys("default")
            stream_env = env if (k1 and k2) else EngineSettings().resolved_binance_env()
            host, port = spot_stream_endpoint(stream_env)

            try:
                from backend.api.grid_manager import grid_manager

                symbols = grid_manager.active_symbols()
            except Exception:
                symbols = []
            if not symbols:
                fallback = str(hub.state.get("symbol") or "DOGEUSDT").upper().replace("/", "")
                symbols = [fallback]

            streams_q = "/".join(_stream_segment_for_symbol(s) for s in symbols)
            stream_url = f"wss://{host}{port}/stream?streams={streams_q}"

            async with websockets.connect(stream_url, ping_interval=20, ping_timeout=60) as ws:
                while not stop.is_set():
                    current_set = frozenset(str(s).upper().replace("/", "") for s in symbols)
                    try:
                        from backend.api.grid_manager import grid_manager as gm

                        wanted = frozenset(gm.active_symbols())
                        if not wanted:
                            wanted = frozenset(
                                [
                                    str(hub.state.get("symbol") or "DOGEUSDT").upper().replace(
                                        "/", ""
                                    )
                                ]
                            )
                    except Exception:
                        wanted = current_set
                    if wanted != current_set:
                        break

                    raw = await asyncio.wait_for(ws.recv(), timeout=120.0)
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        continue

                    payload: dict[str, Any]
                    if "data" in msg and "stream" in msg:
                        payload = msg["data"] if isinstance(msg["data"], dict) else {}
                        stream_name = str(msg.get("stream") or "")
                        inner_sym = stream_name.replace("@ticker", "").upper()
                    else:
                        payload = msg
                        inner_sym = str(hub.state.get("symbol") or "DOGEUSDT").upper().replace("/", "")

                    try:
                        price = float(
                            payload.get("c") or payload.get("lastPrice") or payload.get("price") or 0
                        )
                    except (TypeError, ValueError):
                        continue
                    if price <= 0 or not inner_sym:
                        continue

                    await hub.merge_room(inner_sym, {"markPrice": price})
                    await hub.broadcast_room(
                        inner_sym,
                        {
                            "type": "mark",
                            "markPrice": price,
                            "t": int(payload.get("E", 0) or 0),
                        },
                    )
                    try:
                        from backend.api.grid_manager import grid_manager

                        await grid_manager.dispatch_mark(inner_sym, price)
                    except Exception:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.debug("mark feed reconnect", exc_info=True)
            await asyncio.sleep(1.5)
