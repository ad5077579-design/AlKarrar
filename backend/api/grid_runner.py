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
from backend.core.binance_key_probe import credentials_fingerprint
from backend.api.dashboard_meta import apply_credentials_meta
from backend.api.spot_realized_ledger import (
    SpotGridRealizedLedger,
    journal_row_to_ledger_row,
    validate_band_matches_symbol_mark,
    validate_grid_economics,
    validate_trailing_offset,
)
from backend.main_engine import Side
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
        "profit_injection_mode": "compound_size",
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
        self._allocated_capital: float = 0.0

    @property
    def allocated_capital(self) -> float:
        return float(self._allocated_capital)

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
            "virtualArmed": virtual_armed,
            "virtualExecutions": virtual_executions,
            "exchangeFillsSession": virtual_executions,
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
            st["profitInjectionMode"] = "compound_size"
            st["lastAvailableUsdt"] = round(float(getattr(self._strategy, "_last_available_usdt", 0.0)), 8)
            st["allocatedCapital"] = round(float(self._allocated_capital), 8)
            st["deployCapitalUsdt"] = round(self.effective_deploy_capital_usdt(), 8)
            if hasattr(self._strategy, "vol_profile_snapshot"):
                st["volProfile"] = self._strategy.vol_profile_snapshot()
        st["gridEquityUsdt"] = 0.0
        st["unrealizedPnlUsdt"] = 0.0
        if self._running:
            try:
                mk = float(self._strategy._ram.last_price or 0.0) if self._strategy else 0.0  # noqa: SLF001
            except Exception:
                mk = 0.0
            if mk > 0:
                st["gridEquityUsdt"] = round(self.grid_equity_usdt_sync(mk), 8)
                st["unrealizedPnlUsdt"] = round(self._ledger.unrealized_pnl_usdt(mk), 8)
        return st

    def effective_deploy_capital_usdt(self) -> float:
        """Ring-fenced deploy: allocation + cumulative realized for this symbol only."""
        realized = 0.0
        if self._strategy is not None:
            ram = getattr(self._strategy, "_ram", None)  # noqa: SLF001
            if ram is not None:
                realized = float(getattr(ram, "cumulative_realized_usdt", 0.0) or 0.0)
        return max(float(self._allocated_capital) + realized, 0.0)

    def grid_equity_usdt_sync(self, mark: float) -> float:
        deploy = self.effective_deploy_capital_usdt()
        unreal = 0.0
        if mark > 0 and self._ledger.buy_lots:
            unreal = self._ledger.unrealized_pnl_usdt(mark)
        return max(deploy + unreal, 0.0)

    async def compute_grid_equity_usdt(self) -> float:
        mk = 0.0
        if self._strategy is not None:
            mk = float(getattr(self._strategy._ram, "last_price", 0.0) or 0.0)  # noqa: SLF001
        if mk <= 0 and self._client:
            try:
                tick = await self._client.fetch_ticker(self._symbol)
                mk = float(tick.get("price") or tick.get("lastPrice") or 0.0)
            except Exception:
                mk = 0.0
        return self.grid_equity_usdt_sync(mk)

    async def refresh_dynamic_sizing(
        self,
        mark: float | None = None,
        *,
        force: bool = False,
    ) -> None:
        """Resize lots when cumulative realized profit crosses compound_resize_pct (non-fatal on API errors)."""
        if not self._running or not self._client or not self._strategy:
            return
        deploy = self.effective_deploy_capital_usdt()
        if deploy <= 0:
            return

        mk = float(mark or 0.0)
        if mk <= 0:
            mk = float(self._strategy._ram.last_price or 0.0)  # noqa: SLF001
        if mk <= 0 and self._client:
            try:
                tick = await self._client.fetch_ticker(self._symbol)
                mk = float(tick.get("price") or tick.get("lastPrice") or 0.0)
            except Exception:
                mk = 0.0
        if mk <= 0:
            return

        self._strategy._last_available_usdt = deploy  # noqa: SLF001 — ring-fenced deploy, not wallet
        try:
            changed = self._strategy.refresh_order_size_from_capital(
                deploy_usdt=deploy,
                mark=mk,
                reason="grid_start" if force else "realized_profit",
                force=force,
            )
            if changed:
                self._strategy._rearm_virtual_ladder(reason="dynamic_capital_cycle", mode="qty_only")  # noqa: SLF001
        except Exception as exc:
            _log.warning("refresh_dynamic_sizing strategy update failed: %s", exc)

    async def _broadcast_grid_metrics(self) -> None:
        sym = self._symbol.strip().upper().replace("/", "")
        if not self._running or not sym:
            return
        data = self.status()
        try:
            from backend.api.portfolio_risk import grid_risk_metrics_snapshot

            eq = await self.compute_grid_equity_usdt()
            data = {**data, **grid_risk_metrics_snapshot(symbol=sym, grid_equity_usdt=eq)}
        except Exception:
            pass
        await hub.broadcast_room(
            sym,
            {
                "type": "grid_metrics",
                "symbol": sym,
                "data": data,
            },
        )

    async def start(self, bot_id: str, settings: dict[str, Any], *, resume: bool = False) -> dict[str, Any]:
        key, secret, env, _legacy = await get_binance_keys(bot_id)
        if not key or not secret:
            raise RuntimeError("لا توجد مفاتيح Binance — أضفها في .env أو لوحة المفاتيح")

        sym = str(settings.get("symbol", "DOGEUSDT")).upper().replace("/", "")
        alloc = max(
            float(settings.get("allocatedCapital") or settings.get("initialCapital") or 0.0),
            0.0,
        )
        if alloc <= 0:
            raise RuntimeError("allocatedCapital must be > 0")
        self._allocated_capital = alloc
        settings = {
            **settings,
            "allocatedCapital": alloc,
            "initialCapital": alloc,
            "binanceEnv": env,
            "credentialsFingerprint": credentials_fingerprint(key, secret),
        }

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
        validate_grid_economics(
            generator_upper=float(settings["generatorUpper"]),
            generator_lower=float(settings["generatorLower"]),
            generator_count=int(settings["generatorCount"]),
            allocated_capital=alloc,
        )
        if not resume and not settings.get("resumeFromSnapshot"):
            validate_band_matches_symbol_mark(
                generator_upper=float(settings["generatorUpper"]),
                generator_lower=float(settings["generatorLower"]),
                mark_price=mark,
                symbol=sym,
            )
        trail_off = float(settings.get("trailingOffset") or 0)
        if trail_off > 0 and mark > 0:
            validate_trailing_offset(trailing_offset=trail_off, mark_price=mark)

        resume_snap = settings.get("resumeFromSnapshot")
        if resume or isinstance(resume_snap, dict):
            self._session_start_ms = int(
                settings.get("resumeSessionStartMs")
                or (resume_snap.get("sessionStartMs") if isinstance(resume_snap, dict) else 0)
                or settings.get("resumeUpdatedMs")
                or 0
            )
            if self._session_start_ms <= 0:
                self._session_start_ms = int(time.time() * 1000) - 3600_000
        else:
            self._session_start_ms = int(time.time() * 1000) - 180_000

        from backend.api.grid_live_ledger import grid_live_ledger

        grid_live_ledger.begin_session(sym, bot_id=bot_id)

        strategy = AlKarrarProShiftingGridStrategy(client)
        strategy._quantize_hooks = self._quantize_order  # type: ignore[attr-defined]
        strategy._ledger_cap_sell_cb = self._ledger_cap_sell_qty  # type: ignore[attr-defined]
        strategy._on_exchange_fill_cb = self._on_strategy_exchange_fill  # type: ignore[attr-defined]
        await strategy.on_start(bot_id, settings)

        await self._seed_realized_ledger(client, sym)

        self._client = client
        self._strategy = strategy
        self._bot_id = bot_id
        self._symbol = sym
        self._running = True

        if resume or isinstance(resume_snap, dict):
            since_ms = int(settings.get("resumeUpdatedMs") or 0) - 120_000
            await self._reconcile_downtime_fills(client, sym, bot_id, since_ms=since_ms)
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
                "allocatedCapital": float(alloc),
                "markPrice": mark,
                "exchangeTestnet": env != "mainnet",
                "binanceEnv": env,
            },
        )
        settings_snap = dict(merged)
        await apply_credentials_meta(bot_id, settings_snap)
        await hub.broadcast_room(sym, {"type": "settings", "data": settings_snap})

        _log.info(
            "spot grid started %s [%s, %s] levels=%s mark=%s resume=%s",
            sym,
            settings["generatorLower"],
            settings["generatorUpper"],
            settings["generatorCount"],
            mark,
            bool(resume or settings.get("resumeFromSnapshot")),
        )
        await self.refresh_dynamic_sizing(mark if mark > 0 else None, force=True)

        try:
            from backend.api.portfolio_risk import reset_trailing_equity_baseline_for_grid

            eq0 = self.grid_equity_usdt_sync(mark if mark > 0 else 0.0) or alloc
            reset_trailing_equity_baseline_for_grid(sym, eq0)
        except Exception:
            _log.debug("grid trailing baseline seed skipped", exc_info=True)

        try:
            from backend.api.grid_live_ledger import grid_state_from_strategy, log_grid_ledger

            gs = grid_state_from_strategy(strategy)
            log_grid_ledger(
                strategy,
                action_type="GRID_START",
                trigger_reason=f"تشغيل شبكة Spot على {sym}",
                mark_price=float(mark),
                generator_upper=float(gs["generator_upper"]),
                generator_lower=float(gs["generator_lower"]),
                generator_count=int(gs["generator_count"]),
                order_size=float(gs["order_size"]),
                extra={
                    "generatorUpper": settings.get("generatorUpper"),
                    "generatorLower": settings.get("generatorLower"),
                    "generatorCount": settings.get("generatorCount"),
                },
            )
        except Exception:
            _log.debug("grid ledger GRID_START skipped", exc_info=True)

        try:
            await strategy.persist_resume_snapshot(auto_resume=True)
        except Exception:
            _log.debug("initial resume snapshot write skipped", exc_info=True)

        out = {**self.status(), "settings": settings, "mark": mark}
        try:
            await self._broadcast_grid_metrics()
        except Exception:
            _log.debug("grid_metrics broadcast on start skipped", exc_info=True)
        return out

    def _ledger_cap_sell_qty(self, requested_qty: float) -> float:
        return self._ledger.cap_sell_base_qty(requested_qty)

    async def _on_strategy_exchange_fill(self, side: Side, res: dict[str, Any]) -> None:
        if not self._running:
            return
        async with self._ledger_lock:
            delta = self._ledger.ingest_order_fills(res, symbol=self._symbol)
            self._pending_realized_delta += float(delta)
        asyncio.create_task(
            self._sync_trades_after_fill(),
            name=f"trades-refresh-{self._symbol}",
        )

    async def _sync_trades_after_fill(self) -> None:
        """Push Binance myTrades into SQLite + notify UI (matches demo order history)."""
        sym = self._symbol.strip().upper().replace("/", "")
        if not sym or not self._client:
            return
        try:
            from backend.api.bot_hub import hub
            from backend.api.trade_journal import sync_trades_from_exchange
            from backend.database import async_session_factory

            async with async_session_factory() as db:
                await sync_trades_from_exchange(
                    self._client,
                    db,
                    bot_id=self._bot_id,
                    symbol=sym,
                    limit=80,
                )
            await hub.broadcast_room(sym, {"type": "trades_refresh", "symbol": sym})
        except Exception:
            _log.debug("sync_trades_after_fill failed %s", sym, exc_info=True)

    async def _reconcile_downtime_fills(
        self,
        client: BinanceSpotClient,
        sym: str,
        bot_id: str,
        *,
        since_ms: int,
    ) -> None:
        """REST catch-up for fills that occurred while API was down."""
        start = max(0, int(since_ms))
        try:
            rows = await client.get_account_trades(
                symbol=sym,
                limit=500,
                start_time_ms=start,
            )
        except Exception:
            _log.warning("resume reconcile trades failed %s", sym, exc_info=True)
            return
        if not rows:
            return
        async with self._ledger_lock:
            delta = float(self._ledger.ingest_many(rows))
            self._pending_realized_delta += delta
        try:
            from backend.api.trade_journal import normalize_binance_trade_row, sync_trades_from_exchange
            from backend.database import async_session_factory

            for row in rows:
                n = normalize_binance_trade_row(row)
                if n:
                    await self.ingest_trade_execution_row(n)
            async with async_session_factory() as db:
                await sync_trades_from_exchange(
                    client,
                    db,
                    bot_id=bot_id,
                    symbol=sym,
                    limit=500,
                    start_time_ms=start,
                )
            await hub.broadcast_room(sym, {"type": "trades_refresh", "symbol": sym})
        except Exception:
            _log.debug("resume trade journal sync failed", exc_info=True)
        _log.info("resume reconcile %s trades=%s since_ms=%s", sym, len(rows), start)

    async def _seed_realized_ledger(self, client: BinanceSpotClient, sym: str) -> None:
        raw = await client.get_account_trades(symbol=sym, limit=500)
        probe_fallback: list[dict[str, Any]] = []
        if not raw:
            probe_fallback = await client.get_account_trades(symbol=sym, limit=1)
        async with self._ledger_lock:
            self._ledger = SpotGridRealizedLedger(session_isolated=True)
            self._pending_realized_delta = 0.0
            self._session_realized_usdt = 0.0
            if raw:
                self._ledger.anchor_trade_cursor(raw)
            elif probe_fallback:
                self._ledger.anchor_trade_cursor(probe_fallback)

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
            await self.refresh_dynamic_sizing(mark)

        deploy = self.effective_deploy_capital_usdt()
        payload: dict[str, Any] = {"mark": mark, "price": mark, "deploy_usdt": deploy}
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
            grid_eq = await self.compute_grid_equity_usdt()
            if grid_eq > 0:
                from backend.api.portfolio_risk import maybe_trailing_equity_stop_for_grid

                await maybe_trailing_equity_stop_for_grid(
                    symbol=self._symbol,
                    grid_equity_usdt=grid_eq,
                    bot_id=self._bot_id or "default",
                )
        except Exception:
            _log.debug("grid trailing equity check skipped", exc_info=True)

        try:
            await self._broadcast_grid_metrics()
        except Exception:
            _log.debug("grid_metrics broadcast skipped", exc_info=True)

    async def stop(self, *, manual: bool = True) -> dict[str, Any]:
        bot_id = self._bot_id
        sym = self._symbol
        if self._strategy and bot_id and sym:
            try:
                if manual:
                    await self._strategy.persist_resume_snapshot(auto_resume=False)
                else:
                    await self._strategy.persist_resume_snapshot(auto_resume=True)
            except Exception:
                _log.debug("persist resume snapshot on stop", exc_info=True)
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
        self._allocated_capital = 0.0
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
