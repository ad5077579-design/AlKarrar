"""Multi-grid orchestration: one GridRunner per active symbol."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.api.grid_runner import GridRunner

_log = logging.getLogger(__name__)


class GridManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_symbol: dict[str, GridRunner] = {}

    def active_symbols(self) -> list[str]:
        return sorted(s for s, r in self._by_symbol.items() if r.running)

    def status_dict(self) -> dict[str, dict[str, Any]]:
        return {s: dict(r.status()) for s, r in self._by_symbol.items() if r.running}

    def get_runner(self, symbol: str) -> GridRunner | None:
        return self._by_symbol.get(symbol.strip().upper().replace("/", ""))

    def note_order_placed(self, symbol: str) -> None:
        r = self.get_runner(symbol)
        if r:
            r.note_order_placed()

    async def dispatch_mark(self, symbol: str, price: float) -> None:
        sym = symbol.strip().upper().replace("/", "")
        r = self._by_symbol.get(sym)
        if r and r.running and price > 0:
            asyncio.create_task(r.on_mark(price), name=f"grid-mark-{sym}")

    async def ingest_trade_row(self, bot_id: str, row: dict[str, Any]) -> None:
        sym = str(row.get("symbol", "")).upper().replace("/", "")
        r = self._by_symbol.get(sym)
        if r and r.running and r.grid_bot_id == bot_id:
            await r.ingest_trade_execution_row(row)

    async def start(self, bot_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        sym = str(settings.get("symbol", "DOGEUSDT")).upper().replace("/", "")
        old = self._by_symbol.get(sym)
        if old and old.running:
            await old.stop()
        runner = GridRunner()
        async with self._lock:
            self._by_symbol[sym] = runner
        try:
            return await runner.start(bot_id, settings)
        except Exception:
            async with self._lock:
                if self._by_symbol.get(sym) is runner:
                    del self._by_symbol[sym]
            raise

    async def stop(self, symbol: str | None = None) -> dict[str, Any]:
        if symbol:
            sym = symbol.strip().upper().replace("/", "")
            r = self._by_symbol.get(sym)
            if not r:
                return {"running": False, "symbol": sym, "bot_id": "", "note": "not_running"}
            st = await r.stop()
            async with self._lock:
                self._by_symbol.pop(sym, None)
            return st

        async with self._lock:
            items = list(self._by_symbol.items())
            self._by_symbol.clear()
        stopped: dict[str, Any] = {}
        for sym, r in items:
            try:
                stopped[sym] = await r.stop()
            except Exception:
                _log.exception("grid stop %s", sym)
        return {"stopped": stopped}

    async def stop_all(self) -> None:
        await self.stop(None)


grid_manager = GridManager()
