"""Strategy plugin contract — every live strategy inherits ``BaseStrategy``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.core.base_exchange import BaseExchange


class BaseStrategy(ABC):
    """
    Hook-based lifecycle. Wire the engine to call these from your event loop.

    Implementations must be stateless w.r.t. global singletons; persist via database layer.
    """

    name: str = "base"

    def __init__(self, exchange: BaseExchange | None = None) -> None:
        self._exchange = exchange

    @property
    def exchange(self) -> BaseExchange | None:
        return self._exchange

    @abstractmethod
    async def on_start(self, bot_id: str, settings: dict[str, Any]) -> None:
        """Load config / validate parameters before trading."""

    @abstractmethod
    async def on_stop(self, bot_id: str) -> None:
        """Cancel child tasks, flush state."""

    @abstractmethod
    async def on_tick(self, bot_id: str, market: dict[str, Any]) -> None:
        """Periodic or event-driven decision step (e.g. new candle, book update)."""
