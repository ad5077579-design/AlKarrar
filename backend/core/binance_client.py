"""Binance USD-M Futures via python-binance AsyncClient (async)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp
from binance import AsyncClient

from backend.core.base_exchange import BaseExchange

_log = logging.getLogger(__name__)

# Reference URLs (python-binance picks hosts via ``testnet`` / ``demo`` on AsyncClient).
USD_M_FUTURES_REST_MAINNET = "https://fapi.binance.com"
# Unified Demo USD-M REST root; python-binance appends ``/v{version}/`` + path (do not add ``/v1`` here).
USD_M_FUTURES_REST_DEMO = "https://demo-fapi.binance.com/fapi"
USD_M_FUTURES_REST_TESTNET = "https://testnet.binancefuture.com/fapi"
USD_M_FUTURES_WS_USER_MAINNET = "wss://fstream.binance.com/ws/<listenKey>"
USD_M_FUTURES_WS_USER_TESTNET = "wss://fstream.binancefuture.com/ws/<listenKey>"
USD_M_FUTURES_WS_USER_UNIFIED_DEMO = "wss://demo-fstream.binance.com/ws/<listenKey>"
# Optional marker on ``AsyncClient`` (library does not define it); used for debugging / parity with REST host.
UNIFIED_USDM_DEMO_FUTURES_STREAM_WS = "wss://demo-fstream.binance.com/ws"


def _aiohttp_session_params_for_dns(
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Prefer threaded DNS (getaddrinfo) over aiodns.

    On some Windows setups aiohttp's default AsyncResolver fails with
    ``Could not contact DNS servers`` even when the system browser resolves Binance fine.
    """
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


def futures_client_flags(*, paper: bool, legacy_futures_testnet: bool) -> tuple[bool, bool]:
    """
    Map UI/env "paper" keys to ``BinanceFuturesClient.create`` flags ``(testnet, demo_semantic)``.

    - Mainnet: ``(False, False)``
    - Unified Binance Demo (demo.binance.com keys): ``(False, True)`` → REST/WS routed manually to
      ``demo-fapi`` / ``demo-fstream`` (we never pass ``AsyncClient(..., demo=True)``).
    - Legacy USD-M testnet: ``(True, False)`` → ``testnet.binancefuture.com``
    """
    if not paper:
        return False, False
    if legacy_futures_testnet:
        return True, False
    return False, True


def _apply_unified_usdm_demo_urls(raw: AsyncClient) -> None:
    """Point USD-M REST + stream marker at Unified Demo without enabling library ``demo=True`` (avoids ``demo-api`` spot)."""
    # Base must match ``_create_futures_api_uri``: ``{base}/v1/ping`` etc. (do not bake ``/v1`` into the base).
    raw.FUTURES_URL = USD_M_FUTURES_REST_DEMO
    setattr(raw, "FUTURES_STREAM_URL", UNIFIED_USDM_DEMO_FUTURES_STREAM_WS)


def parse_usdm_futures_balances(acc: dict[str, Any]) -> dict[str, float]:
    """
    Map ``GET /fapi/v2/account`` (``futures_account``) top-level fields to hub / UI.

    Uses Binance field names exactly — no synthetic substitutes for ``availableBalance``.
    ``currentCapital`` / ``marginBalance`` remain aliases used elsewhere in the app.

    Returns: ``totalWalletBalance``, ``totalMarginBalance``, ``availableBalance``, ``floatingPnl``,
    plus aliases ``currentCapital`` (= wallet) and ``marginBalance`` (= total margin).
    """
    def _to_f(key: str) -> float | None:
        if key not in acc or acc.get(key) is None:
            return None
        try:
            return float(acc[key])
        except (TypeError, ValueError):
            return None

    tw = _to_f("totalWalletBalance")
    tm = _to_f("totalMarginBalance")
    av_top = _to_f("availableBalance")
    unreal = _to_f("totalUnrealizedProfit")

    wallet = tw if tw is not None else (tm if tm is not None else 0.0)
    margin = tm if tm is not None else wallet
    if unreal is None:
        unreal = 0.0

    avail: float | None = av_top
    if avail is None and isinstance(acc.get("assets"), list):
        for row in acc["assets"]:
            if not isinstance(row, dict):
                continue
            if str(row.get("asset", "")).upper() != "USDT":
                continue
            try:
                if row.get("availableBalance") is not None:
                    avail = float(row["availableBalance"])
                elif row.get("walletBalance") is not None:
                    avail = float(row["walletBalance"])
            except (TypeError, ValueError):
                avail = None
            break
    if avail is None:
        avail = 0.0

    return {
        "totalWalletBalance": wallet,
        "totalMarginBalance": margin,
        "availableBalance": avail,
        "floatingPnl": unreal,
        "currentCapital": wallet,
        "marginBalance": margin,
    }


def _coerce_futures_server_time_ms(res: Any) -> int:
    """``GET /fapi/v1/time`` returns ``{"serverTime": ...}``; tolerate plain int if the client unwraps."""
    if isinstance(res, dict):
        v = res.get("serverTime", res.get("server_time"))
        if v is None:
            raise TypeError(f"unexpected futures time payload: {res!r}")
        return int(v)
    return int(res)


class BinanceFuturesClient(BaseExchange):
    """
    Thin async adapter over python-binance futures endpoints.

    - ``testnet=False, demo=False`` → production ``fapi.binance.com``.
    - ``testnet=False, demo=True`` (semantic flag from ``futures_client_flags``) → **Unified Binance Demo**
      ``demo-fapi.binance.com``: we keep ``AsyncClient(..., demo=False)`` and patch ``FUTURES_URL`` so the
      library never switches spot/margin to ``demo-api.binance.com`` or pings spot during init.
    - ``testnet=True, demo=False`` → legacy futures testnet ``testnet.binancefuture.com``.

    We do **not** use ``AsyncClient.create()``: that pings **spot** first. We construct ``AsyncClient``
    directly and set ``timestamp_offset`` from ``futures_time()`` only (USD-M REST only; no spot
    ``exchangeInfo`` / ``ping``).

    HTTP uses ``ThreadedResolver`` by default so DNS goes through the OS stack (avoids aiodns
    ``Could not contact DNS servers`` on some Windows machines). Pass ``session_params`` to override.

    Use ``await BinanceFuturesClient.create(...)`` then ``await client.aclose()`` in ``finally``.
    """

    __slots__ = ("_raw", "_unified_usdm_demo")

    def __init__(self, raw: AsyncClient, *, unified_usdm_demo: bool = False) -> None:
        self._raw = raw
        self._unified_usdm_demo = unified_usdm_demo

    @classmethod
    async def create(
        cls,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        testnet: bool = False,
        demo: bool = False,
        requests_params: dict[str, Any] | None = None,
        session_params: dict[str, Any] | None = None,
    ) -> BinanceFuturesClient:
        """
        Build a client. Keys may be omitted for public endpoints only (e.g. ping).

        Do not set ``testnet=True`` and ``demo=True`` together.

        ``demo=True`` here means "Unified USD-M Demo" (demo.binance.com keys). It is **not** passed as
        ``AsyncClient(demo=True)``, which would repoint spot/WS helpers to ``demo-api.binance.com``.
        """
        if testnet and demo:
            raise ValueError("choose only one of testnet=True or demo=True for BinanceFuturesClient")
        unified_usdm_demo = bool(demo) and not bool(testnet)
        raw = AsyncClient(
            api_key or None,
            api_secret or None,
            testnet=testnet,
            demo=False,
            requests_params=requests_params,
            session_params=_aiohttp_session_params_for_dns(session_params),
        )
        if unified_usdm_demo:
            _apply_unified_usdm_demo_urls(raw)
        try:
            # Skip AsyncClient.create(): it calls spot ping + get_server_time (wrong host for futures-only).
            ft = _coerce_futures_server_time_ms(await raw.futures_time())
            raw.timestamp_offset = ft - int(time.time() * 1000)
        except BaseException:
            await raw.close_connection()
            raise
        return cls(raw, unified_usdm_demo=unified_usdm_demo)

    @classmethod
    async def create_for_paper_or_mainnet(
        cls,
        api_key: str | None,
        api_secret: str | None,
        *,
        paper: bool,
        legacy_futures_testnet: bool = False,
        requests_params: dict[str, Any] | None = None,
        session_params: dict[str, Any] | None = None,
    ) -> BinanceFuturesClient:
        """Convenience: ``paper`` matches dashboard ``binanceTestnet`` / ``BINANCE_TESTNET`` (non-mainnet keys)."""
        tn, dm = futures_client_flags(paper=paper, legacy_futures_testnet=legacy_futures_testnet)
        return await cls.create(
            api_key,
            api_secret,
            testnet=tn,
            demo=dm,
            requests_params=requests_params,
            session_params=session_params,
        )

    @property
    def raw(self) -> AsyncClient:
        """Escape hatch for advanced calls not wrapped here."""
        return self._raw

    @property
    def unified_usdm_demo(self) -> bool:
        """True when REST is routed to Unified Demo (``demo-fapi``); WebSockets must use ``demo-fstream``."""
        return self._unified_usdm_demo

    def futures_user_data_stream_url(self, listen_key: str) -> str:
        """USD-M user data WebSocket URL for legacy testnet vs mainnet vs Unified Demo."""
        raw = self._raw
        if getattr(raw, "testnet", False):
            return f"wss://fstream.binancefuture.com/ws/{listen_key}"
        if self._unified_usdm_demo:
            return f"wss://demo-fstream.binance.com/ws/{listen_key}"
        return f"wss://fstream.binance.com/ws/{listen_key}"

    async def futures_listen_key_create(self) -> str:
        return await self._raw.futures_stream_get_listen_key()

    async def futures_listen_key_keepalive(self, listen_key: str) -> None:
        await self._raw.futures_stream_keepalive(listen_key)

    async def futures_listen_key_close(self, listen_key: str) -> None:
        await self._raw.futures_stream_close(listen_key)

    async def ping(self) -> Any:
        return await self._raw.futures_ping()

    async def fetch_server_time_ms(self) -> int:
        return _coerce_futures_server_time_ms(await self._raw.futures_time())

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        data = await self._raw.futures_symbol_ticker(symbol=symbol)
        if not isinstance(data, dict):
            raise TypeError("unexpected futures_symbol_ticker payload")
        return data

    async def futures_klines(
        self,
        *,
        symbol: str,
        interval: str,
        limit: int = 500,
    ) -> list[Any]:
        """USD-M public klines (same shape as ``GET /fapi/v1/klines``)."""
        return await self._raw.futures_klines(symbol=symbol, interval=interval, limit=limit)

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

    async def fetch_account_balance(self) -> dict[str, float]:
        """
        Live wallet snapshot from ``GET /fapi/v2/account`` (via ``futures_account``).

        Returns: totalWalletBalance, totalMarginBalance, availableBalance, floatingPnl,
        plus aliases currentCapital (= wallet) and marginBalance (= total margin).
        """
        acc = await self.fetch_account()
        return parse_usdm_futures_balances(acc)

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
