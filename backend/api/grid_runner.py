"""
Live shifting-grid runner for dashboard bot (Spot only).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from backend.api.bot_hub import hub
from backend.api.credential_resolver import get_binance_keys
from backend.api.dashboard_meta import apply_credentials_meta
from backend.api.spot_realized_ledger import SpotGridRealizedLedger, journal_row_to_ledger_row
from backend.core.binance_client import BinanceSpotClient
from backend.core.exchange_filters import fetch_symbol_filters, normalize_order, quantize_price
from backend.strategies.alkarrar_pro_shifting_grid import AlKarrarProShiftingGridStrategy

_log = logging.getLogger(__name__)


def calibrated_doge_grid_settings(mark: float, *, levels: int = 8, capital_usdt: float = 40.0) -> dict[str, Any]:
    if not (mark > 0):
        mark = 0.15
    half_band = mark * 0.006
    upper = mark + half_band
    lower = mark - half_band
    if lower <= 0:
        lower = mark * 0.992
    gcount = max(3, min(levels, 15))
    return {
        "symbol": "DOGEUSDT",
        "generatorUpper": upper,
        "generatorLower": lower,
        "generatorCount": gcount,
        "maxGeneratorCount": min(24, max(8, levels + 4)),
        "initialCapital": capital_usdt,
        "trailingOffset": max(mark * 0.0025, 1e-6),
        "compoundingFactor": 0.05,
        "lift_above_offset": max(mark * 3e-5, 1e-6),
        "trailing_stop_pct": 0.008,
        "profit_injection_mode": "expand_count",
        "boundary_epsilon_pct": 0.0008,
    }


class GridRunner:
    def __init__(self) -> None:
        self._client: BinanceSpotClient | None = None
        self._strategy: AlKarrarProShiftingGridStrategy | None = None
        self._bot_id: str = ""
        self._symbol: str = ""
        self._filters: dict[str, float] = {}
        self._running = False
        self._started_at: str | None = None
        self._last_tick_at: str | None = None
        self._last_error: str = ""
        self._orders_placed: int = 0
        self._ledger_lock = asyncio.Lock()
        self._ledger = SpotGridRealizedLedger()
        self._pending_realized_delta: float = 0.0
        self._session_start_ms: int = 0
        self._session_realized_usdt: float = 0.0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def grid_bot_id(self) -> str:
        return self._bot_id

    @property
    def grid_symbol(self) -> str:
        return self._symbol

    def status(self) -> dict[str, Any]:
        virtual_armed = 0
        virtual_executions = 0
        vb = getattr(self._strategy, "_virtual_book", None) if self._strategy else None
        if vb is not None:
            virtual_armed = vb.armed_count()
            virtual_executions = int(vb.executions)
        st: dict[str, Any] = {
            "running": self._running,
            "bot_id": self._bot_id,
            "symbol": self._symbol,
            "startedAt": self._started_at,
            "lastTickAt": self._last_tick_at,
            "lastError": self._last_error,
            "ordersPlaced": virtual_armed if vb is not None else self._orders_placed,
            "virtualExecutions": virtual_executions,
            "virtualGrid": vb is not None,
            "sessionRealizedUsdt": round(float(self._session_realized_usdt), 8),
        }
        if self._strategy is not None:
            st["lineTrail"] = self._strategy.line_trail_snapshot()
            ram = self._strategy._ram  # noqa: SLF001
            st["cumulativeRealizedUsdt"] = round(float(ram.cumulative_realized_usdt), 8)
            st["trailingOffset"] = float(ram.trailingOffset)
            st["compoundingFactor"] = float(ram.compoundingFactor)
            st["generatorCount"] = int(ram.generatorCount)
            st["maxGeneratorCount"] = int(getattr(self._strategy, "_max_generator_count", 0))
        return st

    async def _broadcast_grid_metrics(self) -> None:
        sym = self._symbol.strip().upper().replace("/", "")
        if not self._running or not sym:
            return
        await hub.broadcast_room(
            sym,
            {
                "type": "grid_metrics",
                "symbol": sym,
                "data": self.status(),
            },
        )

    async def start(self, bot_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        key, secret, env, _legacy = await get_binance_keys(bot_id)
        if not key or not secret:
            raise RuntimeError("لا توجد مفاتيح Binance — أضفها في .env أو لوحة المفاتيح")

        sym = str(settings.get("symbol", "DOGEUSDT")).upper().replace("/", "")
        client = await BinanceSpotClient.create_for_env(
            api_key=key,
            api_secret=secret,
            env=env,
        )
        self._filters = await fetch_symbol_filters(client, sym)

        tick = await client.fetch_ticker(sym)
        mark = 0.0
        for k in ("price", "lastPrice", "last"):
            try:
                mark = float(tick.get(k) or 0)
            except (TypeError, ValueError):
                mark = 0.0
            if mark > 0:
                break

        upper = float(settings["generatorUpper"])
        lower = float(settings["generatorLower"])
        settings = {
            **settings,
            "symbol": sym,
            "generatorUpper": quantize_price(upper, self._filters["tick_size"]),
            "generatorLower": quantize_price(lower, self._filters["tick_size"]),
        }

        self._session_start_ms = int(time.time() * 1000) - 180_000

        strategy = AlKarrarProShiftingGridStrategy(client)
        strategy._quantize_hooks = self._quantize_order  # type: ignore[attr-defined]
        await strategy.on_start(bot_id, settings)

        await self._seed_realized_ledger(client, sym)

        self._client = client
        self._strategy = strategy
        self._bot_id = bot_id
        self._symbol = sym
        self._running = True
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._last_error = ""
        self._orders_placed = 0

        mg = settings.get("maxGeneratorCount")
        max_gen = int(mg) if mg is not None else 9999

        merged = await hub.merge_room(
            sym,
            {
                "symbol": sym,
                "generatorUpper": settings["generatorUpper"],
                "generatorLower": settings["generatorLower"],
                "generatorCount": int(settings["generatorCount"]),
                "maxGeneratorCount": max_gen,
                "initialCapital": float(settings["initialCapital"]),
                "markPrice": mark,
                "exchangeTestnet": env != "mainnet",
                "binanceEnv": env,
            },
        )
        settings_snap = dict(merged)
        await apply_credentials_meta(bot_id, settings_snap)
        await hub.broadcast_room(sym, {"type": "settings", "data": settings_snap})

        _log.info(
            "spot grid started %s [%s, %s] levels=%s mark=%s",
            sym,
            settings["generatorLower"],
            settings["generatorUpper"],
            settings["generatorCount"],
            mark,
        )
        out = {**self.status(), "settings": settings, "mark": mark}
        try:
            await self._broadcast_grid_metrics()
        except Exception:
            _log.debug("grid_metrics broadcast on start skipped", exc_info=True)
        return out

    async def _seed_realized_ledger(self, client: BinanceSpotClient, sym: str) -> None:
        raw = await client.get_account_trades(symbol=sym, limit=500)
        probe_fallback: list[dict[str, Any]] = []
        if not raw:
            probe_fallback = await client.get_account_trades(symbol=sym, limit=1)
        async with self._ledger_lock:
            self._ledger = SpotGridRealizedLedger()
            self._pending_realized_delta = 0.0
            self._session_realized_usdt = 0.0
            if raw:
                self._ledger.seed_history(raw, mute_realized=True)
            elif probe_fallback:
                mx = max(int(t["id"]) for t in probe_fallback if isinstance(t, dict))
                self._ledger.last_trade_id = mx

    async def _poll_new_trades_realized(self) -> float:
        if not self._client:
            return 0.0
        sym = self._symbol
        rows = await self._client.get_account_trades(
            symbol=sym,
            limit=250,
            start_time_ms=self._session_start_ms,
        )
        async with self._ledger_lock:
            tail = int(self._ledger.last_trade_id)
            fresh = [r for r in rows if int(r.get("id", 0)) > tail]
            return float(self._ledger.ingest_many(fresh))

    async def ingest_trade_execution_row(self, row: dict[str, Any]) -> None:
        if not self._running:
            return
        rsym = str(row.get("symbol", "")).upper().replace("/", "")
        if rsym and rsym != self._symbol.upper().replace("/", ""):
            return
        lj = journal_row_to_ledger_row(row)
        if lj is None:
            return
        async with self._ledger_lock:
            delta = float(self._ledger.ingest_normalized(lj, mute_realized=False))
            self._pending_realized_delta += delta

    async def _drain_pending_realized(self) -> float:
        async with self._ledger_lock:
            out = float(self._pending_realized_delta)
            self._pending_realized_delta = 0.0
            return out

    def _quantize_order(self, price: float, qty: float) -> tuple[str, str]:
        return normalize_order(price, qty, self._filters)

    async def on_mark(self, mark: float, *, realized_delta_override: float | None = None) -> None:
        if not self._running or not self._strategy or mark <= 0:
            return
        self._last_tick_at = datetime.now(timezone.utc).isoformat()
        polled = await self._poll_new_trades_realized()
        pending = await self._drain_pending_realized()
        realized = polled + pending if realized_delta_override is None else float(realized_delta_override)
        if realized != 0.0:
            self._session_realized_usdt += float(realized)

        payload: dict[str, Any] = {"mark": mark, "price": mark}
        if realized != 0.0:
            payload["realized_delta"] = realized

        try:
            await self._strategy.on_tick(self._bot_id, payload)
        except Exception as exc:
            self._last_error = str(exc)
            _log.warning("grid on_tick: %s", exc)
            from backend.api.audit_log_service import SYSTEM_ERROR, schedule_bot_audit_event

            schedule_bot_audit_event(
                self._bot_id or "default",
                SYSTEM_ERROR,
                symbol=self._symbol or "",
                mark_price=float(mark),
                details={
                    "context": "grid_strategy_on_tick",
                    "error": str(exc)[:4096],
                },
            )
        try:
            await self._broadcast_grid_metrics()
        except Exception:
            _log.debug("grid_metrics broadcast skipped", exc_info=True)

    async def stop(self) -> dict[str, Any]:
        bot_id = self._bot_id
        sym = self._symbol
        if self._strategy and bot_id:
            try:
                await self._strategy.on_stop(bot_id)
            except Exception:
                _log.exception("grid on_stop")
        if self._client and sym:
            try:
                await self._client.cancel_all_open_orders(symbol=sym)
            except Exception:
                _log.debug("cancel all on grid stop", exc_info=True)
            await self._client.aclose()

        self._strategy = None
        self._client = None
        self._running = False
        self._bot_id = ""
        self._symbol = ""
        async with self._ledger_lock:
            self._ledger = SpotGridRealizedLedger()
            self._pending_realized_delta = 0.0
            self._session_realized_usdt = 0.0
        if sym:
            await hub.remove_room(sym)
        _log.info("spot grid stopped")
        return self.status()

    def note_order_placed(self) -> None:
        self._orders_placed += 1
