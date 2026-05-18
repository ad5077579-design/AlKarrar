"""Multi-grid orchestration: one GridRunner per active symbol."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from backend.api.binance_pool import get_spot_client
from backend.api.grid_runner import GridRunner
from backend.api.spot_realized_ledger import validate_band_matches_symbol_mark, validate_grid_economics

_log = logging.getLogger(__name__)

INSUFFICIENT_BALANCE = "Insufficient Live Balance"


def _resolve_allocated_capital(settings: dict[str, Any]) -> float:
    raw = settings.get("allocatedCapital", settings.get("initialCapital"))
    try:
        return max(0.0, float(raw or 0.0))
    except (TypeError, ValueError):
        return 0.0


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

    async def validate_grid_allocation(
        self,
        bot_id: str,
        symbol: str,
        allocated_usdt: float,
        *,
        exclude_symbol: str | None = None,
    ) -> float:
        """Ensure allocation fits live available USDT minus other active grids."""
        alloc = float(allocated_usdt)
        if alloc <= 0:
            raise RuntimeError("allocatedCapital must be > 0")

        client = await get_spot_client(bot_id)
        if client is None:
            raise RuntimeError("لا توجد مفاتيح Binance — أضفها في .env أو لوحة المفاتيح")

        bal = await client.fetch_account_balance()
        available = max(float(bal.get("availableBalance") or 0.0), 0.0)
        if alloc > available + 1e-6:
            raise RuntimeError(INSUFFICIENT_BALANCE)

        sym_ex = (exclude_symbol or symbol).strip().upper().replace("/", "")
        reserved = 0.0
        for s, runner in self._by_symbol.items():
            if not runner.running:
                continue
            if s == sym_ex:
                continue
            reserved += float(runner.allocated_capital)

        if alloc + reserved > available + 1e-6:
            raise RuntimeError(INSUFFICIENT_BALANCE)

        return available

    def note_order_placed(self, symbol: str) -> None:
        r = self.get_runner(symbol)
        if r:
            r.note_order_placed()

    async def dispatch_mark(self, symbol: str, price: float) -> None:
        sym = symbol.strip().upper().replace("/", "")
        r = self._by_symbol.get(sym)
        if r and r.running and price > 0:
            asyncio.create_task(r.on_mark(price), name=f"grid-mark-{sym}")

    async def refresh_active_capital(self) -> None:
        """Refresh order sizes for all running grids from live Spot balances."""
        for sym, r in list(self._by_symbol.items()):
            if r.running:
                try:
                    await r.refresh_dynamic_sizing()
                except Exception:
                    _log.exception("refresh_active_capital %s", sym)

    async def ingest_trade_row(self, bot_id: str, row: dict[str, Any]) -> None:
        sym = str(row.get("symbol", "")).upper().replace("/", "")
        r = self._by_symbol.get(sym)
        if r and r.running and r.grid_bot_id == bot_id:
            await r.ingest_trade_execution_row(row)

    def total_allocated_usdt(self, *, exclude_symbol: str | None = None) -> float:
        ex = exclude_symbol.strip().upper().replace("/", "") if exclude_symbol else ""
        total = 0.0
        for s, r in self._by_symbol.items():
            if not r.running:
                continue
            if ex and s == ex:
                continue
            total += float(r.allocated_capital)
        return total

    async def start(
        self,
        bot_id: str,
        settings: dict[str, Any],
        *,
        resume: bool = False,
    ) -> dict[str, Any]:
        sym = str(settings.get("symbol", "DOGEUSDT")).upper().replace("/", "")
        allocated = _resolve_allocated_capital(settings)
        old = self._by_symbol.get(sym)
        if old and old.running:
            await old.stop()
        await self.validate_grid_allocation(bot_id, sym, allocated, exclude_symbol=sym)
        settings = {
            **settings,
            "allocatedCapital": allocated,
            "initialCapital": allocated,
        }
        validate_grid_economics(
            generator_upper=float(settings["generatorUpper"]),
            generator_lower=float(settings["generatorLower"]),
            generator_count=int(settings["generatorCount"]),
            allocated_capital=allocated,
        )
        runner = GridRunner()
        async with self._lock:
            self._by_symbol[sym] = runner
        try:
            return await runner.start(bot_id, settings, resume=resume)
        except Exception:
            async with self._lock:
                if self._by_symbol.get(sym) is runner:
                    del self._by_symbol[sym]
            raise

    async def stop(self, symbol: str | None = None, *, manual: bool = True) -> dict[str, Any]:
        from backend.api.grid_live_ledger import grid_live_ledger
        from backend.api.grid_snapshot_store import set_auto_resume

        if symbol:
            sym = symbol.strip().upper().replace("/", "")
            r = self._by_symbol.get(sym)
            if not r:
                if manual:
                    grid_live_ledger.flush_manual_stop(sym)
                    await set_auto_resume("default", sym, enabled=False)
                return {"running": False, "symbol": sym, "bot_id": "", "note": "not_running"}
            bot_id = r.grid_bot_id or "default"
            if manual:
                await set_auto_resume(bot_id, sym, enabled=False)
            st = await r.stop(manual=manual)
            async with self._lock:
                self._by_symbol.pop(sym, None)
            if manual:
                grid_live_ledger.flush_manual_stop(sym)
            from backend.api.portfolio_risk import clear_grid_risk_slot

            clear_grid_risk_slot(sym)
            return st

        async with self._lock:
            items = list(self._by_symbol.items())
            self._by_symbol.clear()
        stopped: dict[str, Any] = {}
        for sym, r in items:
            try:
                if manual:
                    await set_auto_resume(r.grid_bot_id or "default", sym, enabled=False)
                stopped[sym] = await r.stop(manual=manual)
            except Exception:
                _log.exception("grid stop %s", sym)
            if manual:
                grid_live_ledger.flush_manual_stop(sym)
            from backend.api.portfolio_risk import clear_grid_risk_slot

            clear_grid_risk_slot(sym)
        return {"stopped": stopped}

    async def stop_all(self, *, manual: bool = True) -> None:
        await self.stop(None, manual=manual)


grid_manager = GridManager()
