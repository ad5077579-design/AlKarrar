"""Binance Spot via python-binance AsyncClient (async)."""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp
from binance import AsyncClient

from backend.core.base_exchange import BaseExchange
from backend.core.binance_env import (
    BinanceSpotEnv,
    env_display_label,
    normalize_binance_env,
    spot_stream_endpoint,
)

_log = logging.getLogger(__name__)


def _aiohttp_session_params_for_dns(
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import asyncio

    loop = asyncio.get_running_loop()
    out: dict[str, Any] = {
        "connector": aiohttp.TCPConnector(
            resolver=aiohttp.ThreadedResolver(loop=loop),
            force_close=False,
            enable_cleanup_closed=True,
        ),
    }
    if extra:
        out.update(extra)
    return out


def _coerce_server_time_ms(res: Any) -> int:
    if isinstance(res, dict):
        v = res.get("serverTime", res.get("server_time"))
        if v is None:
            raise TypeError(f"unexpected time payload: {res!r}")
        return int(v)
    return int(res)


def parse_spot_balances(acc: dict[str, Any]) -> dict[str, float]:
    """Map ``GET /api/v3/account`` balances to hub fields (USDT wallet)."""
    free_usdt = 0.0
    locked_usdt = 0.0
    for row in acc.get("balances") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("asset", "")).upper() != "USDT":
            continue
        try:
            free_usdt = float(row.get("free") or 0)
            locked_usdt = float(row.get("locked") or 0)
        except (TypeError, ValueError):
            pass
        break
    total = free_usdt + locked_usdt
    return {
        "totalWalletBalance": total,
        "totalMarginBalance": total,
        "availableBalance": free_usdt,
        "floatingPnl": 0.0,
        "currentCapital": total,
        "marginBalance": total,
    }


def spot_stream_host(*, env: BinanceSpotEnv | None = None, testnet: bool | None = None) -> str:
    """Public WS host for ticker streams (legacy ``testnet=`` or ``env=``)."""
    if env is None:
        env = "testnet" if testnet else "mainnet"
    host, _port = spot_stream_endpoint(env)
    return host


class BinanceSpotClient(BaseExchange):
    """
    Thin async adapter over Binance **Spot** REST + user stream.

    Environment is selected via ``BINANCE_ENV`` (``mainnet`` | ``testnet`` | ``demo``)
    or legacy ``paper`` / ``BINANCE_TESTNET`` (testnet vs mainnet only).
    """

    __slots__ = ("_raw", "_env")

    def __init__(self, raw: AsyncClient, *, env: BinanceSpotEnv = "mainnet") -> None:
        self._raw = raw
        self._env = env

    @classmethod
    async def create(
        cls,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        env: BinanceSpotEnv = "mainnet",
        requests_params: dict[str, Any] | None = None,
        session_params: dict[str, Any] | None = None,
    ) -> BinanceSpotClient:
        use_testnet = env == "testnet"
        use_demo = env == "demo"
        if use_testnet and use_demo:
            raise ValueError("invalid env")
        raw = AsyncClient(
            api_key or None,
            api_secret or None,
            testnet=use_testnet,
            demo=use_demo,
            requests_params=requests_params,
            session_params=_aiohttp_session_params_for_dns(session_params),
        )
        try:
            st = _coerce_server_time_ms(await raw.get_server_time())
            raw.timestamp_offset = st - int(time.time() * 1000)
        except BaseException:
            await raw.close_connection()
            raise
        return cls(raw, env=env)

    @classmethod
    async def create_for_env(
        cls,
        api_key: str | None,
        api_secret: str | None,
        *,
        env: BinanceSpotEnv,
        requests_params: dict[str, Any] | None = None,
        session_params: dict[str, Any] | None = None,
    ) -> BinanceSpotClient:
        return await cls.create(
            api_key,
            api_secret,
            env=env,
            requests_params=requests_params,
            session_params=session_params,
        )

    @classmethod
    async def create_for_paper_or_mainnet(
        cls,
        api_key: str | None,
        api_secret: str | None,
        *,
        paper: bool,
        env: BinanceSpotEnv | None = None,
        legacy_futures_testnet: bool = False,  # ignored — spot only
        requests_params: dict[str, Any] | None = None,
        session_params: dict[str, Any] | None = None,
    ) -> BinanceSpotClient:
        _ = legacy_futures_testnet
        resolved = env if env is not None else ("testnet" if paper else "mainnet")
        return await cls.create(
            api_key,
            api_secret,
            env=resolved,
            requests_params=requests_params,
            session_params=session_params,
        )

    @property
    def raw(self) -> AsyncClient:
        return self._raw

    @property
    def env(self) -> BinanceSpotEnv:
        return self._env

    @property
    def testnet(self) -> bool:
        """True when REST uses testnet.binance.vision (not demo)."""
        return self._env == "testnet"

    @property
    def demo(self) -> bool:
        return self._env == "demo"

    def user_data_stream_url(self, listen_key: str) -> str:
        host, port = spot_stream_endpoint(self._env)
        return f"wss://{host}{port}/ws/{listen_key}"

    async def listen_key_create(self) -> str:
        return await self._raw.stream_get_listen_key()

    async def listen_key_keepalive(self, listen_key: str) -> None:
        await self._raw.stream_keepalive(listen_key)

    async def listen_key_close(self, listen_key: str) -> None:
        await self._raw.stream_close(listen_key)

    async def ping(self) -> Any:
        return await self._raw.ping()

    async def fetch_server_time_ms(self) -> int:
        return _coerce_server_time_ms(await self._raw.get_server_time())

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        data = await self._raw.get_symbol_ticker(symbol=symbol)
        if not isinstance(data, dict):
            raise TypeError("unexpected get_symbol_ticker payload")
        price = data.get("price")
        return {**data, "lastPrice": price, "price": price}

    async def get_klines(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int = 500,
    ) -> list[Any]:
        return await self._raw.get_klines(symbol=symbol, interval=interval, limit=limit)

    async def get_exchange_info(self) -> dict[str, Any]:
        data = await self._raw.get_exchange_info()
        if not isinstance(data, dict):
            raise TypeError("unexpected get_exchange_info payload")
        return data

    async def get_tickers_24hr(self) -> list[dict[str, Any]]:
        data = await self._raw.get_ticker()
        if not isinstance(data, list):
            raise TypeError("unexpected get_ticker payload")
        return [row for row in data if isinstance(row, dict)]

    async def get_account_trades(
        self,
        *,
        symbol: str,
        limit: int = 100,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        from_id: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"symbol": symbol, "limit": max(1, min(int(limit), 1000))}
        if from_id is not None:
            params["fromId"] = int(from_id)
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        data = await self._raw.get_my_trades(**params)
        if not isinstance(data, list):
            raise TypeError("unexpected get_my_trades payload")
        return [row for row in data if isinstance(row, dict)]

    async def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        data = await self._raw.get_open_orders(symbol=symbol)
        if not isinstance(data, list):
            raise TypeError("unexpected get_open_orders payload")
        return [row for row in data if isinstance(row, dict)]

    async def get_all_open_orders(self) -> list[dict[str, Any]]:
        """Open orders across all symbols (Spot ``GET /api/v3/openOrders`` without symbol)."""
        data = await self._raw.get_open_orders()
        if not isinstance(data, list):
            raise TypeError("unexpected get_open_orders payload")
        return [row for row in data if isinstance(row, dict)]

    async def cancel_all_open_orders(self, symbol: str) -> Any:
        return await self._raw.cancel_all_open_orders(symbol=symbol)

    async def cancel_order(self, symbol: str, order_id: int) -> Any:
        return await self._raw.cancel_order(symbol=symbol, orderId=order_id)

    async def fetch_positions(self) -> list[dict[str, Any]]:
        """Spot has no futures positions — non-zero wallet balances only."""
        acc = await self.fetch_account()
        out: list[dict[str, Any]] = []
        for row in acc.get("balances") or []:
            if not isinstance(row, dict):
                continue
            try:
                free = float(row.get("free") or 0)
                locked = float(row.get("locked") or 0)
            except (TypeError, ValueError):
                continue
            qty = free + locked
            if qty <= 0:
                continue
            asset = str(row.get("asset", "")).upper()
            if asset in ("USDT", "BUSD", "USDC"):
                continue
            out.append({"asset": asset, "quantity": qty, "free": free, "locked": locked})
        return out

    async def fetch_account(self) -> dict[str, Any]:
        data = await self._raw.get_account()
        if not isinstance(data, dict):
            raise TypeError("unexpected get_account payload")
        return data

    async def fetch_account_balance(self) -> dict[str, float]:
        return parse_spot_balances(await self.fetch_account())

    def base_asset_free(self, acc: dict[str, Any], symbol: str) -> float:
        sym = symbol.upper().replace("/", "")
        base = sym[:-4] if sym.endswith("USDT") else sym
        for row in acc.get("balances") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("asset", "")).upper() == base:
                try:
                    return float(row.get("free") or 0)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    async def create_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float | str,
        price: float | str | None = None,
        time_in_force: str | None = None,
        reduce_only: bool | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        _ = reduce_only  # spot has no reduce-only flag
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
            **extra,
        }
        if price is not None:
            params["price"] = price
        if time_in_force is not None:
            params["timeInForce"] = time_in_force
        data = await self._raw.create_order(**params)
        if not isinstance(data, dict):
            raise TypeError("unexpected create_order payload")
        _log.info(
            "spot_order_created env=%s symbol=%s orderId=%s",
            self._env,
            symbol,
            data.get("orderId"),
        )
        return data

    async def aclose(self) -> None:
        await self._raw.close_connection()


# Re-export for callers
__all__ = [
    "BinanceClient",
    "BinanceSpotClient",
    "BinanceSpotEnv",
    "env_display_label",
    "normalize_binance_env",
    "parse_spot_balances",
    "spot_stream_host",
]

# Single client type for the whole app (spot-only).
BinanceClient = BinanceSpotClient
