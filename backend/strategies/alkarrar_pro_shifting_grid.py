"""
AlKarrar_Pro_Shifting_Grid — dynamic grid lift, profit injection, trailing TP, boundary lot expansion.

Hot path: mutate RAM synchronously in ``on_tick``; exchange I/O and SQLite writes are ``asyncio.create_task`` / queue worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from backend.core.binance_client import BinanceFuturesClient
from backend.main_engine import GeneratorBand, Side, build_dca_levels
from backend.project_paths import data_dir
from backend.strategies.base_strategy import BaseStrategy

_log = logging.getLogger(__name__)

ProfitInjectionMode = Literal["expand_count", "compound_size"]


class LineTrailPhase(str, Enum):
    idle = "idle"
    lock_profit = "lock_profit"
    trailing = "trailing"


@dataclass
class LineTrailState:
    """Per-grid-line trailing state (RAM only until persisted)."""

    phase: LineTrailPhase = LineTrailPhase.idle
    tp_level: float = 0.0
    lock_floor: float = 0.0
    trail_peak: float = 0.0


@dataclass
class ShiftingGridRAM:
    """All fast-path state; contractual generator* names mirrored in JSON snapshots."""

    generatorUpper: float
    generatorLower: float
    generatorCount: int
    initialCapital: float
    trailingOffset: float
    compoundingFactor: float

    last_price: float = 0.0
    cumulative_realized_usdt: float = 0.0
    profit_bank_usdt: float = 0.0
    injections_done: int = 0

    order_quantity_base: float = 0.0
    order_quantity_effective: float = 0.0
    lot_expansion_multiplier: float = 1.0

    boundary_mode: bool = False
    open_orders_lower: list[int] = field(default_factory=list)
    open_orders_upper: list[int] = field(default_factory=list)
    line_trail: dict[int, LineTrailState] = field(default_factory=dict)


class AlKarrarProShiftingGridStrategy(BaseStrategy):
    """
    Shifting grid with profit reinjection, LockProfit + trailing take-profit, and boundary lot sizing.

    ``on_tick`` expects ``market`` to include at least ``price`` or ``mark`` (float).
    Optional keys (caller / adapter): ``realized_delta`` (USDT realized since last tick),
    ``position_qty_base`` aggregate base qty for exit sizing heuristics.
    """

    name = "alkarrar_pro_shifting_grid"

    def __init__(self, exchange: BinanceFuturesClient | None = None) -> None:
        super().__init__(exchange)
        self._bot_id = ""
        self._symbol = "BTCUSDT"
        self._first_side = Side.BUY
        self._ram = ShiftingGridRAM(
            generatorUpper=0.0,
            generatorLower=0.0,
            generatorCount=2,
            initialCapital=0.0,
            trailingOffset=0.0,
            compoundingFactor=0.0,
        )
        self._running = False
        self._lift_above_offset: float = 0.0
        self._trailing_stop_pct: float = 0.01
        self._boundary_epsilon_pct: float = 0.0005
        self._profit_injection_mode: ProfitInjectionMode = "expand_count"
        self._boundary_reinvest_frac: float = 0.25
        self._lot_expand_step_pct: float = 0.05
        self._db_path = data_dir() / "trader.db"
        self._db_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1)
        self._db_worker: asyncio.Task[None] | None = None

    async def on_start(self, bot_id: str, settings: dict[str, Any]) -> None:
        if not isinstance(self._exchange, BinanceFuturesClient):
            raise TypeError("AlKarrarProShiftingGridStrategy requires BinanceFuturesClient")
        self._bot_id = bot_id
        self._running = True
        self._symbol = str(settings.get("symbol", "BTCUSDT")).upper().replace("/", "")
        self._first_side = Side.BUY if str(settings.get("first_side", "buy")).lower() != "sell" else Side.SELL

        upper = float(settings["generatorUpper"])
        lower = float(settings["generatorLower"])
        count = int(settings["generatorCount"])
        if count < 2 or not (lower < upper):
            raise ValueError("require generatorLower < generatorUpper and generatorCount >= 2")

        ic = float(settings["initialCapital"])
        trail_off = float(settings["trailingOffset"])
        comp = float(settings["compoundingFactor"])

        band = GeneratorBand(generatorUpper=upper, generatorLower=lower, generatorCount=count)
        levels = build_dca_levels(band)
        slice_usdt = ic / max(len(levels), 1)
        mid = (upper + lower) / 2.0
        qty_base = slice_usdt / max(mid, 1e-12)

        self._ram = ShiftingGridRAM(
            generatorUpper=upper,
            generatorLower=lower,
            generatorCount=count,
            initialCapital=ic,
            trailingOffset=trail_off,
            compoundingFactor=comp,
            order_quantity_base=qty_base,
            order_quantity_effective=qty_base,
        )
        self._init_line_trail_states(levels)

        default_lift = max(upper * 1.0e-4, (upper - lower) * 0.02)
        self._lift_above_offset = float(settings.get("lift_above_offset", default_lift))
        self._trailing_stop_pct = float(settings.get("trailing_stop_pct", 0.01))
        self._boundary_epsilon_pct = float(settings.get("boundary_epsilon_pct", 0.0005))
        mode = str(settings.get("profit_injection_mode", "expand_count")).lower()
        self._profit_injection_mode = "compound_size" if mode == "compound_size" else "expand_count"
        self._boundary_reinvest_frac = float(settings.get("boundary_reinvest_frac", 0.25))
        self._lot_expand_step_pct = float(settings.get("lot_expand_step_pct", 0.05))

        await _ensure_shifting_grid_table(self._db_path)
        self._db_worker = asyncio.create_task(self._db_worker_loop(), name="shifting-grid-db")

        asyncio.create_task(self._bootstrap_open_grid(), name="shifting-grid-bootstrap")

    async def on_stop(self, bot_id: str) -> None:
        self._running = False
        if self._db_worker:
            try:
                self._db_queue.put_nowait(None)
            except asyncio.QueueFull:
                try:
                    _ = self._db_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self._db_queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._db_worker, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                self._db_worker.cancel()
            self._db_worker = None

    async def on_tick(self, bot_id: str, market: dict[str, Any]) -> None:
        price = _price_from_market(market)
        if price <= 0.0:
            return

        rd = market.get("realized_delta")
        if rd is not None:
            try:
                d = float(rd)
            except (TypeError, ValueError):
                d = 0.0
            if d != 0.0:
                self._ram.cumulative_realized_usdt += d
                if d > 0:
                    self._ram.profit_bank_usdt += d * self._boundary_reinvest_frac

        self._ram.last_price = price

        self._boundary_eval(price)
        self._profit_injection_eval()
        lifted = self._lift_eval_and_mutate_ram(price)
        if lifted:
            asyncio.create_task(self._io_dynamic_lift(), name="shifting-lift-io")

        self._trailing_eval(price)
        for idx in self._consume_trailing_exits(price):
            asyncio.create_task(self._exit_line_market(idx), name=f"trail-exit-{idx}")

        self._schedule_db_snapshot()

    # --- RAM-only helpers ----------------------------------------------------

    def _init_line_trail_states(self, levels: list[float]) -> None:
        self._ram.line_trail.clear()
        for i, lv in enumerate(levels):
            tp = lv + self._ram.trailingOffset if self._first_side == Side.BUY else lv - self._ram.trailingOffset
            self._ram.line_trail[i] = LineTrailState(phase=LineTrailPhase.idle, tp_level=tp, lock_floor=0.0, trail_peak=0.0)

    def _rebuild_levels(self) -> list[float]:
        band = GeneratorBand(
            generatorUpper=self._ram.generatorUpper,
            generatorLower=self._ram.generatorLower,
            generatorCount=self._ram.generatorCount,
        )
        return build_dca_levels(band)

    def _boundary_eval(self, price: float) -> None:
        lo = self._ram.generatorLower
        eps = max(lo * self._boundary_epsilon_pct, 1e-9)
        if price <= lo + eps:
            if not self._ram.boundary_mode:
                _log.info("boundary_mode: price touched lower band")
            self._ram.boundary_mode = True
            step = self._lot_expand_step_pct * max(self._ram.profit_bank_usdt, 0.0) / max(
                self._ram.initialCapital, 1e-9
            )
            if step > 0:
                self._ram.lot_expansion_multiplier += min(step, self._lot_expand_step_pct)
                self._ram.profit_bank_usdt *= 1.0 - self._boundary_reinvest_frac

    def _profit_injection_eval(self) -> None:
        n = max(self._ram.generatorCount, 1)
        cost_per_level = self._ram.initialCapital / n
        if cost_per_level <= 0:
            return
        threshold = (self._ram.injections_done + 1) * cost_per_level
        if self._ram.cumulative_realized_usdt < threshold:
            return
        self._ram.injections_done += 1
        if self._profit_injection_mode == "expand_count":
            self._ram.generatorCount += 1
            levels = self._rebuild_levels()
            self._init_line_trail_states(levels)
            slice_usdt = self._ram.initialCapital / max(len(levels), 1)
            mid = (self._ram.generatorUpper + self._ram.generatorLower) / 2.0
            self._ram.order_quantity_effective = slice_usdt / max(mid, 1e-12)
        else:
            f = max(self._ram.compoundingFactor, 0.0)
            self._ram.order_quantity_effective *= 1.0 + f
        _log.info(
            "profit_injection mode=%s count=%s qty_eff=%.8f",
            self._profit_injection_mode,
            self._ram.generatorCount,
            self._ram.order_quantity_effective,
        )

    def _lift_eval_and_mutate_ram(self, price: float) -> bool:
        if price < self._ram.generatorUpper + self._lift_above_offset:
            return False
        band = self._ram.generatorUpper - self._ram.generatorLower
        if band <= 0:
            return False
        new_upper = price
        new_lower = new_upper - band
        self._ram.generatorUpper = new_upper
        self._ram.generatorLower = new_lower
        levels = self._rebuild_levels()
        self._init_line_trail_states(levels)
        _log.info("grid_shift new_upper=%s new_lower=%s", new_upper, new_lower)
        return True

    def _trailing_eval(self, price: float) -> None:
        """
        TP touch: LockProfit (no immediate close). Next ticks: arm trailing stop at X% (``trailing_stop_pct``)
        off the momentum peak (``trail_peak`` ratchets with favorable movement).
        """
        for idx, st in self._ram.line_trail.items():
            if st.phase == LineTrailPhase.idle:
                if self._first_side == Side.BUY and price >= st.tp_level:
                    st.phase = LineTrailPhase.lock_profit
                    st.lock_floor = st.tp_level
                    _log.debug("line %s lock_profit floor=%s", idx, st.lock_floor)
                elif self._first_side == Side.SELL and price <= st.tp_level:
                    st.phase = LineTrailPhase.lock_profit
                    st.lock_floor = st.tp_level
                    _log.debug("line %s lock_profit floor=%s", idx, st.lock_floor)
            elif st.phase == LineTrailPhase.lock_profit:
                st.phase = LineTrailPhase.trailing
                st.trail_peak = price
                _log.debug("line %s trailing armed peak=%s", idx, st.trail_peak)
            elif st.phase == LineTrailPhase.trailing:
                if self._first_side == Side.BUY and price > st.trail_peak:
                    st.trail_peak = price
                elif self._first_side == Side.SELL and price < st.trail_peak:
                    st.trail_peak = price

    def _consume_trailing_exits(self, price: float) -> list[int]:
        """Detect trailing stop breach (sync); return line indices for async MARKET reduce-only."""
        fired: list[int] = []
        for idx, st in list(self._ram.line_trail.items()):
            if st.phase != LineTrailPhase.trailing or st.trail_peak <= 0:
                continue
            if self._first_side == Side.BUY:
                thr = st.trail_peak * (1.0 - self._trailing_stop_pct)
                if price < thr:
                    fired.append(idx)
                    st.phase = LineTrailPhase.idle
                    st.trail_peak = 0.0
                    st.lock_floor = 0.0
            else:
                thr = st.trail_peak * (1.0 + self._trailing_stop_pct)
                if price > thr:
                    fired.append(idx)
                    st.phase = LineTrailPhase.idle
                    st.trail_peak = 0.0
                    st.lock_floor = 0.0
        return fired

    def _effective_order_qty(self) -> float:
        m = self._ram.lot_expansion_multiplier if self._ram.boundary_mode else 1.0
        return self._ram.order_quantity_effective * m

    def _schedule_db_snapshot(self) -> None:
        blob = json.dumps(
            {
                "strategy": self.name,
                "generatorUpper": self._ram.generatorUpper,
                "generatorLower": self._ram.generatorLower,
                "generatorCount": self._ram.generatorCount,
                "initialCapital": self._ram.initialCapital,
                "trailingOffset": self._ram.trailingOffset,
                "compoundingFactor": self._ram.compoundingFactor,
                "last_price": self._ram.last_price,
                "cumulative_realized_usdt": self._ram.cumulative_realized_usdt,
                "profit_bank_usdt": self._ram.profit_bank_usdt,
                "boundary_mode": self._ram.boundary_mode,
                "lot_expansion_multiplier": self._ram.lot_expansion_multiplier,
                "order_quantity_effective": self._ram.order_quantity_effective,
                "injections_done": self._ram.injections_done,
            },
            separators=(",", ":"),
        )
        try:
            self._db_queue.put_nowait(blob)
        except asyncio.QueueFull:
            try:
                self._db_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._db_queue.put_nowait(blob)
            except asyncio.QueueFull:
                pass

    # --- async I/O (never block hot path) ----------------------------------

    async def _bootstrap_open_grid(self) -> None:
        if not isinstance(self._exchange, BinanceFuturesClient):
            return
        levels = self._rebuild_levels()
        qty = self._effective_order_qty()
        mid = (self._ram.generatorLower + self._ram.generatorUpper) / 2.0
        for i, px in enumerate(levels):
            if self._ram.boundary_mode and px < mid:
                continue
            side = self._first_side if i % 2 == 0 else (Side.SELL if self._first_side == Side.BUY else Side.BUY)
            asyncio.create_task(self._place_and_track(px, side, qty, bucket="all"), name=f"boot-{i}")

    async def _io_dynamic_lift(self) -> None:
        """Cancel lower-rung open orders; place new orders at the lifted top."""
        if not isinstance(self._exchange, BinanceFuturesClient):
            return
        for oid in list(self._ram.open_orders_lower):
            asyncio.create_task(self._safe_cancel(oid), name=f"cx-{oid}")
        self._ram.open_orders_lower.clear()

        levels = self._rebuild_levels()
        top_idx = len(levels) - 1
        top_px = levels[top_idx]
        side = self._first_side if top_idx % 2 == 0 else (Side.SELL if self._first_side == Side.BUY else Side.BUY)
        qty = self._effective_order_qty()
        asyncio.create_task(self._place_and_track(top_px, side, qty, bucket="upper"), name="lift-top")

    async def _exit_line_market(self, line_idx: int) -> None:
        if not isinstance(self._exchange, BinanceFuturesClient):
            return
        qty = self._effective_order_qty()
        side = Side.SELL if self._first_side == Side.BUY else Side.BUY
        try:
            await self._exchange.create_order(
                symbol=self._symbol,
                side=side.value.upper(),
                order_type="MARKET",
                quantity=qty,
                reduce_only=True,
            )
        except Exception:
            _log.exception("trail exit failed line=%s", line_idx)

    async def _place_and_track(self, price: float, side: Side, qty: float, bucket: Literal["all", "lower", "upper"]) -> None:
        if not isinstance(self._exchange, BinanceFuturesClient) or not self._running:
            return
        try:
            res = await self._exchange.create_order(
                symbol=self._symbol,
                side=side.value.upper(),
                order_type="LIMIT",
                quantity=qty,
                price=price,
                time_in_force="GTC",
            )
            oid = res.get("orderId")
            if oid is None:
                return
            oid_i = int(oid)
            lo = self._ram.generatorLower
            hi = self._ram.generatorUpper
            span = max(hi - lo, 1e-12)
            if bucket == "lower" or (bucket == "all" and abs(price - lo) <= span * 0.08):
                self._ram.open_orders_lower.append(oid_i)
            if bucket == "upper" or (bucket == "all" and abs(price - hi) <= span * 0.08):
                self._ram.open_orders_upper.append(oid_i)
        except Exception:
            _log.exception("place_and_track failed price=%s", price)

    async def _safe_cancel(self, order_id: int) -> None:
        if not isinstance(self._exchange, BinanceFuturesClient):
            return
        try:
            await self._exchange.raw.futures_cancel_order(symbol=self._symbol, orderId=order_id)
        except Exception:
            _log.debug("cancel failed id=%s", order_id, exc_info=True)

    async def _db_worker_loop(self) -> None:
        while True:
            item = await self._db_queue.get()
            if item is None:
                break
            try:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute(
                        """
                        INSERT INTO shifting_grid_snapshots (bot_id, payload, updated_ms)
                        VALUES (?, ?, ?)
                        ON CONFLICT(bot_id) DO UPDATE SET
                          payload = excluded.payload,
                          updated_ms = excluded.updated_ms
                        """,
                        (self._bot_id, item, int(time.time() * 1000)),
                    )
                    await db.commit()
            except Exception:
                _log.exception("shifting_grid db write failed")


def _price_from_market(market: dict[str, Any]) -> float:
    for k in ("price", "mark", "last", "close"):
        v = market.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


async def _ensure_shifting_grid_table(path: Path) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS shifting_grid_snapshots (
              bot_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              updated_ms INTEGER NOT NULL
            )
            """
        )
        await db.commit()
