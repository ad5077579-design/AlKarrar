"""Reuse one Spot REST client per key set (avoids connect storm every sync tick)."""



from __future__ import annotations



import asyncio

import logging



from backend.api.credential_resolver import get_binance_keys

from backend.core.binance_client import BinanceSpotClient

from backend.core.binance_env import BinanceSpotEnv



_log = logging.getLogger(__name__)



_lock = asyncio.Lock()

_cached_sig: tuple[str, str, BinanceSpotEnv] | None = None

_cached_client: BinanceSpotClient | None = None





async def get_spot_client(bot_id: str = "default") -> BinanceSpotClient | None:

    key, secret, env, _legacy = await get_binance_keys(bot_id)

    if not key or not secret:

        return None

    sig = (key, secret, env)

    global _cached_sig, _cached_client

    async with _lock:

        if _cached_sig == sig and _cached_client is not None:

            return _cached_client

        if _cached_client is not None:

            try:

                await _cached_client.aclose()

            except Exception:

                _log.debug("pool close previous client", exc_info=True)

        client = await BinanceSpotClient.create_for_env(

            api_key=key,

            api_secret=secret,

            env=env,

        )

        _cached_sig = sig

        _cached_client = client

        return client





async def invalidate_spot_client() -> None:

    global _cached_sig, _cached_client

    async with _lock:

        if _cached_client is not None:

            try:

                await _cached_client.aclose()

            except Exception:

                pass

        _cached_sig = None

        _cached_client = None





async def reset_pool_after_credentials_change() -> None:

    await invalidate_spot_client()

