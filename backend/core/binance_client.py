"""Binance USD-M Futures via python-binance AsyncClient (async)."""

from __future__ import annotations

import logging
from typing import Any

from binance import AsyncClient

from backend.core.base_exchange import BaseExchange

_log = logging.getLogger(__name__)


class BinanceFuturesClient(BaseExchange):
    """
    Thin async adapter over python-binance futures endpoints.

    Use ``await BinanceFuturesClient.create(...)`` then ``await client.aclose()`` in ``finally``.
    """

    __slots__ = ("_raw",)

    def __init__(self, raw: AsyncClient) -> None:
        self._raw = raw

    @classmethod
    async def create(
        cls,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        testnet: bool = True,
        requests_params: dict[str, Any] | None = None,
    ) -> BinanceFuturesClient:
        """
        Build a client. Keys may be omitted for public endpoints only (e.g. ping).

        ``testnet=True`` routes to Binance futures testnet when supported by the library.
        """
        raw = await AsyncClient.create(
            api_key or None,
            api_secret or None,
            testnet=testnet,
            requests_params=requests_params,
        )
        return cls(raw)

    @property
    def raw(self) -> AsyncClient:
        """Escape hatch for advanced calls not wrapped here."""
        return self._raw

    async def ping(self) -> Any:
        return await self._raw.futures_ping()

    async def fetch_server_time_ms(self) -> int:
        return int(await self._raw.futures_time())

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        data = await self._raw.futures_symbol_ticker(symbol=symbol)
        if not isinstance(data, dict):
            raise TypeError("unexpected futures_symbol_ticker payload")
        return data

    async def fetch_positions(self) -> list[dict[str, Any]]:
        data = await self._raw.futures_position_information()
        if not isinstance(data, list):
            raise TypeError("unexpected futures_position_information payload")
        return [row for row in data if isinstance(row, dict)]

    async def fetch_account(self) -> dict[str, Any]:
        data = await self._raw.futures_account()
        if not isinstance(data, dict):
            raise TypeError("unexpected futures_account payload")
        return data

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
        if reduce_only is not None:
            params["reduceOnly"] = reduce_only
        data = await self._raw.futures_create_order(**params)
        if not isinstance(data, dict):
            raise TypeError("unexpected futures_create_order payload")
        _log.info("futures_order_created symbol=%s orderId=%s", symbol, data.get("orderId"))
        return data

    async def aclose(self) -> None:
        await self._raw.close_connection()
