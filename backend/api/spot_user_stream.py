"""Binance Spot user data stream (listenKey) → account sync + trade journal."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from backend.api import spot_account_sync
from backend.api.credential_resolver import get_binance_keys
from backend.api.trade_journal import persist_and_broadcast_spot_execution
from backend.core.binance_client import BinanceSpotClient

_log = logging.getLogger(__name__)


async def _handle_event(msg: dict[str, Any], bot_id: str) -> None:
    et = msg.get("e")
    if et in ("outboundAccountPosition", "balanceUpdate", "executionReport"):
        if et == "executionReport" and str(msg.get("x", "")).upper() == "TRADE":
            await persist_and_broadcast_spot_execution(bot_id, msg)
        await spot_account_sync.sync_spot_account_to_hub_once(bot_id, metrics_source="user_stream")


async def _keepalive(stop: asyncio.Event, client: BinanceSpotClient, listen_key: str) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=30 * 60)
            return
        except TimeoutError:
            pass
        try:
            await client.listen_key_keepalive(listen_key)
        except Exception:
            _log.warning("listenKey keepalive failed", exc_info=True)


async def run_spot_user_stream(stop: asyncio.Event, *, bot_id: str = "default") -> None:
    if os.getenv("ALKARRAR_USER_STREAM", "true").lower() not in ("1", "true", "yes"):
        return
    try:
        import websockets
    except ImportError:
        _log.warning("websockets not installed; user stream disabled")
        return

    while not stop.is_set():
        k1, k2, env, _legacy = await get_binance_keys(bot_id)
        if not k1 or not k2:
            try:
                await asyncio.wait_for(stop.wait(), timeout=5.0)
            except TimeoutError:
                continue
            continue

        client: BinanceSpotClient | None = None
        keep_task: asyncio.Task | None = None
        try:
            client = await BinanceSpotClient.create_for_env(
                api_key=k1,
                api_secret=k2,
                env=env,
            )
            listen_key = await client.listen_key_create()
            url = client.user_data_stream_url(listen_key)
            _log.info("spot user stream connecting env=%s", env)
            keep_task = asyncio.create_task(_keepalive(stop, client, listen_key), name="listenkey-keepalive")
            async with websockets.connect(url, ping_interval=20, ping_timeout=120) as ws:
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), timeout=180.0)
                    msg = json.loads(raw)
                    if isinstance(msg, dict):
                        await _handle_event(msg, bot_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.warning("spot user stream reconnect", exc_info=True)
            await asyncio.sleep(2.0)
        finally:
            if keep_task is not None:
                keep_task.cancel()
                try:
                    await keep_task
                except asyncio.CancelledError:
                    pass
            if client is not None:
                await client.aclose()


run_futures_user_stream = run_spot_user_stream
