"""Optional public mark WebSocket (always on by default). REST sync may also write mark when API keys exist."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from backend.api.bot_hub import hub
from backend.api.credential_resolver import get_binance_keys

_log = logging.getLogger(__name__)


async def run_mark_price_feed(stop: asyncio.Event) -> None:
    if os.getenv("ALKARRAR_MARK_FEED", "true").lower() not in ("1", "true", "yes"):
        return
    # Always run public mark stream as well: when REST sync fails (keys / permissions),
    # the dashboard still gets a live mark for the chart and metrics display.
    try:
        import websockets
    except ImportError:
        _log.warning("websockets not installed; mark feed disabled")
        return

    while not stop.is_set():
        try:
            k1, k2, paper, legacy = await get_binance_keys("default")
            use_paper = bool(paper) if (k1 and k2) else False
            # Legacy futures testnet mark; Unified Demo paper uses demo-fstream; mainnet uses fstream.
            if use_paper and legacy:
                host = "fstream.binancefuture.com"
            elif use_paper:
                host = "demo-fstream.binance.com"
            else:
                host = "fstream.binance.com"
            sym = str(hub.state.get("symbol") or "DOGEUSDT").lower().replace("/", "")
            stream_url = f"wss://{host}/market/ws/{sym}@markPrice@1s"
            async with websockets.connect(stream_url, ping_interval=20, ping_timeout=60) as ws:
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), timeout=120.0)
                    msg = json.loads(raw)
                    if isinstance(msg, dict) and "data" in msg:
                        msg = msg["data"]
                    if not isinstance(msg, dict) or msg.get("e") != "markPriceUpdate":
                        continue
                    try:
                        price = float(msg.get("p", 0) or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if price <= 0:
                        continue
                    await hub.merge_state({"markPrice": price})
                    await hub.broadcast({"type": "mark", "markPrice": price, "t": int(msg.get("E", 0) or 0)})
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.debug("mark feed reconnect", exc_info=True)
            await asyncio.sleep(1.5)
