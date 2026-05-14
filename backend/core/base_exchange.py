"""Abstract exchange adapter — strategies depend on this contract, not on Binance."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseExchange(ABC):
    """Minimal async surface for futures-style trading; extend per venue."""

    @abstractmethod
    async def ping(self) -> Any:
        """Connectivity / server health."""

    @abstractmethod
    async def fetch_server_time_ms(self) -> int:
        """Exchange server time in milliseconds."""

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        """Latest price / 24h stats for `symbol` (venue-native symbol, e.g. BTCUSDT)."""

    @abstractmethod
    async def fetch_positions(self) -> list[dict[str, Any]]:
        """Open positions (empty list if none)."""

    @abstractmethod
    async def fetch_account(self) -> dict[str, Any]:
        """Account / margin summary (venue-native shape)."""

    @abstractmethod
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
        """Place an order; `extra` forwards venue-specific flags."""

    @abstractmethod
    async def aclose(self) -> None:
        """Release HTTP sessions / sockets."""
