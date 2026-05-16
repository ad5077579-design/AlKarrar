"""
AlKarrar_Pro_Shifting_Grid — dynamic grid lift, profit injection, trailing TP, boundary lot expansion.

Hot path: mutate RAM synchronously in ``on_tick``; exchange I/O and SQLite writes are ``asyncio.create_task`` / queue worker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from backend.core.exchange_filters import fetch_symbol_filters, normalize_order, quantize_price
from backend.core.binance_client import BinanceSpotClient, parse_spot_balances
from backend.main_engine import GeneratorBand, Side, build_dca_levels
from backend.project_paths import data_dir
from backend.strategies.base_strategy import BaseStrategy
from backend.strategies.virtual_grid_book import (
    ExecOrderStyle,
    VirtualGridBook,
    VirtualGridLine,
    grid_exec_settings,
)
from backend.api.audit_log_service import (
    GRID_SHIFT,
    PROFIT_INJECT_COMPOUND,
    PROFIT_INJECT_EXPAND,
    SYSTEM_ERROR,
    TAKE_PROFIT_MARKET,
    TRAILING_STARTED,
    schedule_bot_audit_event,
)

_log = logging.getLogger(__name__)

# عمولة شبكة بينانس Spot الافتراضية (ماركت/تيكر) — تقدير محافظ لتغطية العمولة المقتطعة من أصل الأساس بعد الشراء.
# يمكن تجاوزها بـ ALKARRAR_SPOT_TAKER_FEE_RATIO=0.001
def _bootstrap_taker_buy_fee_decimal() -> Decimal:
    raw = (os.getenv("ALKARRAR_SPOT_TAKER_FEE_RATIO") or "").strip()
    try:
        f = Decimal(raw) if raw else Decimal("0.001")
    except Exception:
        f = Decimal("0.001")
    if not (Decimal("0") < f < Decimal("1")):
        return Decimal("0.001")
    return f


def _dec_str(s: str) -> Decimal:
    return Decimal(s)


def _normalize_pair_exact(price: float, qty: float, filters: dict[str, float]) -> tuple[str, str]:
    """تطبيق PRICE_FILTER و LOT_SIZE + NOTIONAL بلا تقريبات خارج normalize_order."""
    return normalize_order(float(price), float(qty), filters)


def market_buy_quantity_string_covers_net_base(
    mark_px: float,
    *,
    filters: dict[str, float],
    net_base_need: Decimal,
    fee_take_from_received_base: Decimal,
    max_iter: int = 100_000,
) -> tuple[str | None, str]:
    """
    أصغر كمية أساس شبكية بحيث: gross_normalized * (1 - fee) >= net_base_need
    (تفترض خصم العمولة من أصل الأساس المستلم — حالة محافظة شائعة).
    """
    if net_base_need <= 0:
        return None, "(no-net)"
    denom = Decimal(1) - fee_take_from_received_base
    if denom <= 0:
        return None, "invalid-fee"

    mk = float(mark_px)
    if not (mk > 0):
        return None, "bad-mark"

    step = Decimal(str(filters.get("step_size") or 0))

    tentative = net_base_need / denom
    guess_qty = float(tentative)
    last_qs = ""
    gross_dec = Decimal(0)

    for _ in range(max_iter):
        p_s, q_s = _normalize_pair_exact(mk, guess_qty, filters)
        if not q_s or float(q_s) <= 0:
            return None, "quantize-zero-qty"
        if q_s == last_qs and gross_dec > 0:
            if gross_dec * denom >= net_base_need:
                return q_s, "same-step-ok"
            if step <= 0:
                return None, "no-step-progress"
            return None, "stalled-step"
        last_qs = q_s
        gross_dec = _dec_str(q_s)
        if gross_dec * denom >= net_base_need:
            return q_s, "ok"
        # زيادة دقيقة: +خطوة LOT بعد التكميم أو نسبة دنيا إن لم توجد خطوة
        if step > 0:
            guess_qty = float(gross_dec + step)
        else:
            guess_qty = float(gross_dec * Decimal("1.000001"))
    return None, "max-iter"


async def _await_free_base_raise(
    client: BinanceSpotClient,
    symbol: str,
    baseline_free_base: Decimal,
    min_gain: Decimal,
    *,
    timeout_s: float = 25.0,
    poll_s: float = 0.2,
) -> tuple[Decimal | None, str]:
    deadline = time.monotonic() + timeout_s
    last_fb: Decimal | None = None
    while time.monotonic() < deadline:
        acc = await client.fetch_account()
        fb_now = Decimal(str(client.base_asset_free(acc, symbol)))
        last_fb = fb_now
        if fb_now - baseline_free_base >= min_gain:
            return fb_now, "ok"
        await asyncio.sleep(poll_s)
    return last_fb, "timeout"

ProfitInjectionMode = Literal["expand_count", "compound_size"]
DcaMode = Literal["equal", "log"]
RearmMode = Literal["full", "qty_only"]


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

    def __init__(self, exchange: BinanceSpotClient | None = None) -> None:
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
        self._hybrid_line_cap: bool = False
        self._max_generator_count: int = 999999
        self._line_exit_mutex: set[int] = set()
        self._db_path = data_dir() / "trader.db"
        self._db_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1)
        self._db_worker: asyncio.Task[None] | None = None
        self._filters: dict[str, float] = {}
        self._virtual_book: VirtualGridBook | None = None
        self._prev_mark: float = 0.0
        self._dca_mode: DcaMode = "equal"

    async def on_start(self, bot_id: str, settings: dict[str, Any]) -> None:
        if not isinstance(self._exchange, BinanceSpotClient):
            raise TypeError("AlKarrarProShiftingGridStrategy requires BinanceSpotClient")
        self._bot_id = bot_id
        self._running = True
        self._symbol = str(settings.get("symbol", "BTCUSDT")).upper().replace("/", "")
        self._virtual_book = VirtualGridBook.from_env(self._symbol)
        slip = settings.get("max_slippage_pct")
        if slip is not None and self._virtual_book is not None:
            try:
                self._virtual_book.max_slippage_pct = max(0.0, float(slip))
            except (TypeError, ValueError):
                pass
        self._prev_mark = 0.0
        dca = str(settings.get("dca_mode", "equal")).lower()
        self._dca_mode = "log" if dca == "log" else "equal"
        self._first_side = Side.BUY if str(settings.get("first_side", "buy")).lower() != "sell" else Side.SELL

        upper = float(settings["generatorUpper"])
        lower = float(settings["generatorLower"])
        count = int(settings["generatorCount"])
        if count < 2 or not (lower < upper):
            raise ValueError("require generatorLower < generatorUpper and generatorCount >= 2")

        self._hybrid_line_cap = "maxGeneratorCount" in settings
        self._max_generator_count = max(int(settings.get("maxGeneratorCount") or count), count)
        if not self._hybrid_line_cap:
            self._max_generator_count = 999999

        self._filters = await fetch_symbol_filters(self._exchange, self._symbol)
        upper = quantize_price(upper, self._filters.get("tick_size", 0))
        lower = quantize_price(lower, self._filters.get("tick_size", 0))
        if not (lower < upper):
            raise ValueError("quantized band invalid for symbol filters")

        ic = float(settings["initialCapital"])
        trail_off = float(settings["trailingOffset"])
        comp = float(settings["compoundingFactor"])

        band = GeneratorBand(generatorUpper=upper, generatorLower=lower, generatorCount=count)
        levels = build_dca_levels(band, mode=self._dca_mode)
        tick = self._filters.get("tick_size", 0)
        levels = [quantize_price(lv, tick) for lv in levels]
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
        if self._virtual_book is not None:
            self._virtual_book.lines.clear()
        self._virtual_book = None
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
                    frac = (
                        self._boundary_reinvest_frac
                        if (not self._hybrid_line_cap or self._generator_at_line_cap())
                        else 0.0
                    )
                    self._ram.profit_bank_usdt += d * frac

        prev_mark = self._prev_mark if self._prev_mark > 0 else price
        self._ram.last_price = price

        self._boundary_eval(price)
        self._profit_injection_eval()
        lifted = self._lift_eval_and_mutate_ram(price)
        if lifted:
            asyncio.create_task(self._io_dynamic_lift(), name="shifting-lift-io")

        self._trailing_eval(price)
        for idx, snap in self._consume_trailing_exits(price):
            asyncio.create_task(self._exit_line_market(idx, snap), name=f"trail-exit-{idx}")

        crossed = self._virtual_crossed(prev_mark, price)
        for ln in crossed[:1]:
            asyncio.create_task(
                self._execute_virtual_line(ln, mark=price),
                name=f"vgrid-fill-{ln.line_index}",
            )

        self._prev_mark = price
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
        levels = build_dca_levels(band, mode=getattr(self, "_dca_mode", "equal"))
        tick = self._filters.get("tick_size", 0)
        return [quantize_price(lv, tick) for lv in levels]

    def line_trail_snapshot(self) -> list[dict[str, Any]]:
        """Public snapshot for grid status / WS (per line_index trailing phase)."""
        rows: list[dict[str, Any]] = []
        for idx in sorted(self._ram.line_trail.keys()):
            st = self._ram.line_trail[idx]
            rows.append(
                {
                    "lineIndex": int(idx),
                    "phase": st.phase.value,
                    "tpLevel": float(st.tp_level),
                    "trailPeak": float(st.trail_peak),
                    "lockFloor": float(st.lock_floor),
                }
            )
        return rows

    def _classify_levels_by_mark(
        self, mark: float
    ) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
        """Split grid indices into virtual BUY (at/below mark) and SELL (above mark) rows."""
        levels = self._rebuild_levels()
        qty_eff = self._effective_order_qty()
        mid = (self._ram.generatorLower + self._ram.generatorUpper) / 2.0
        tick_sz = float(self._filters.get("tick_size") or 0)
        eps = max(tick_sz * 0.5, mark * 1e-12, 1e-12) if mark > 0 else max(tick_sz * 0.5, 1e-12)

        level_rows: list[tuple[int, float]] = []
        for i, px in enumerate(levels):
            if self._ram.boundary_mode and px < mid:
                continue
            level_rows.append((i, px))

        upper: list[tuple[int, float]] = []
        lower: list[tuple[int, float]] = []
        neutral: list[tuple[int, float]] = []
        if mark > 0:
            for row in level_rows:
                idx, px = row
                if px > mark + eps:
                    upper.append(row)
                elif px < mark - eps:
                    lower.append(row)
                else:
                    neutral.append(row)
        else:
            neutral = list(level_rows)

        buy_levels = lower + neutral
        sell_levels = upper
        return buy_levels, sell_levels

    def _rearm_virtual_ladder(self, *, reason: str, mode: RearmMode = "full") -> None:
        """
        Keep virtual RAM aligned after expand / compound / lift.

        ``qty_only``: refresh normalized qty on armed lines (compound).
        ``full``: drop non-triggered armed lines and re-register from current band + mark.
        """
        if not getattr(self, "_running", False) or not self._virtual_book:
            return
        qty_eff = self._effective_order_qty()
        if mode == "qty_only":
            updated = 0
            for ln in self._virtual_book.lines.values():
                if ln.triggered or not ln.armed:
                    continue
                try:
                    _, qty_s = self._normalize_order(ln.price, qty_eff)
                    ln.qty_s = qty_s
                    updated += 1
                except Exception:
                    continue
            self._audit(
                "VIRTUAL_REARM",
                details={
                    "reason": reason,
                    "mode": mode,
                    "lines_updated": updated,
                    "order_quantity_effective": float(self._ram.order_quantity_effective),
                },
            )
            return

        mark = float(self._ram.last_price or 0)
        triggered_idx = {idx for idx, ln in self._virtual_book.lines.items() if ln.triggered}
        for idx in list(self._virtual_book.lines.keys()):
            if idx not in triggered_idx:
                del self._virtual_book.lines[idx]

        if mark <= 0:
            self._audit(
                "VIRTUAL_REARM",
                details={"reason": reason, "mode": mode, "note": "skipped_no_mark"},
            )
            return

        buy_rows, sell_rows = self._classify_levels_by_mark(mark)
        lo = self._ram.generatorLower
        hi = self._ram.generatorUpper
        span = max(hi - lo, 1e-12)
        registered = 0

        def _register_row(idx: int, px: float, side: Side) -> None:
            nonlocal registered
            if idx in triggered_idx:
                return
            try:
                price_s, qty_s = self._normalize_order(px, qty_eff)
            except Exception:
                return
            if float(qty_s) <= 0 or float(price_s) <= 0:
                return
            bucket: Literal["all", "lower", "upper"] = "all"
            if abs(px - lo) <= span * 0.08:
                bucket = "lower"
            elif abs(px - hi) <= span * 0.08:
                bucket = "upper"
            self._virtual_book.register(
                line_index=idx,
                price=px,
                price_s=price_s,
                qty_s=qty_s,
                side=side,
                bucket=bucket,
            )
            registered += 1

        for idx, px in buy_rows:
            _register_row(idx, px, Side.BUY)
        for idx, px in sell_rows:
            _register_row(idx, px, Side.SELL)

        self._audit(
            "VIRTUAL_REARM",
            details={
                "reason": reason,
                "mode": mode,
                "lines_registered": registered,
                "armed_total": self._virtual_book.armed_count(),
                "generatorCount": int(self._ram.generatorCount),
                "mark": mark,
            },
        )

    def _normalize_order(self, price: float, qty: float) -> tuple[str, str]:
        hook = getattr(self, "_quantize_hooks", None)
        if callable(hook):
            p, q = hook(price, qty)
            if isinstance(p, str) and isinstance(q, str):
                return p, q
        return normalize_order(price, qty, self._filters)

    def _audit(
        self,
        event_type: str,
        *,
        realized_usdt: float = 0.0,
        details: dict[str, Any] | None = None,
    ) -> None:
        d = dict(details or {})
        d.setdefault("symbol", getattr(self, "_symbol", ""))
        d.setdefault("strategy", self.name)
        schedule_bot_audit_event(
            getattr(self, "_bot_id", None) or "default",
            event_type,
            symbol=getattr(self, "_symbol", "") or "",
            mark_price=float(self._ram.last_price or 0.0),
            realized_usdt=float(realized_usdt or 0.0),
            details=d,
        )

    def _generator_at_line_cap(self) -> bool:
        return self._ram.generatorCount >= self._max_generator_count

    def _should_expand_generator_on_injection(self) -> bool:
        if self._hybrid_line_cap:
            return self._ram.generatorCount < self._max_generator_count
        return self._profit_injection_mode == "expand_count"

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
        if self._should_expand_generator_on_injection():
            prev_count = int(self._ram.generatorCount)
            self._ram.generatorCount += 1
            levels = self._rebuild_levels()
            self._init_line_trail_states(levels)
            slice_usdt = self._ram.initialCapital / max(len(levels), 1)
            mid = (self._ram.generatorUpper + self._ram.generatorLower) / 2.0
            self._ram.order_quantity_effective = slice_usdt / max(mid, 1e-12)
            self._audit(
                PROFIT_INJECT_EXPAND,
                details={
                    "generatorCount_before": prev_count,
                    "generatorCount_after": int(self._ram.generatorCount),
                    "injections_done": int(self._ram.injections_done),
                    "hybrid_line_cap": self._hybrid_line_cap,
                    "maxGeneratorCount": self._max_generator_count,
                    "cumulative_realized_usdt_after": round(self._ram.cumulative_realized_usdt, 8),
                },
            )
            self._rearm_virtual_ladder(reason="profit_inject_expand", mode="full")
        else:
            prev_qty = float(self._ram.order_quantity_effective)
            f = max(self._ram.compoundingFactor, 0.0)
            self._ram.order_quantity_effective *= 1.0 + f
            self._audit(
                PROFIT_INJECT_COMPOUND,
                details={
                    "order_quantity_effective_before": prev_qty,
                    "order_quantity_effective_after": float(self._ram.order_quantity_effective),
                    "compounding_factor_applied": f,
                    "injections_done": int(self._ram.injections_done),
                    "generatorCount": int(self._ram.generatorCount),
                    "cumulative_realized_usdt_after": round(self._ram.cumulative_realized_usdt, 8),
                },
            )
            self._rearm_virtual_ladder(reason="profit_inject_compound", mode="qty_only")
        _log.info(
            "profit_injection hybrid=%s mode=%s count=%s max=%s qty_eff=%.8f",
            self._hybrid_line_cap,
            self._profit_injection_mode,
            self._ram.generatorCount,
            self._max_generator_count,
            self._ram.order_quantity_effective,
        )

    def _lift_eval_and_mutate_ram(self, price: float) -> bool:
        if price < self._ram.generatorUpper + self._lift_above_offset:
            return False
        band = self._ram.generatorUpper - self._ram.generatorLower
        if band <= 0:
            return False
        old_upper = float(self._ram.generatorUpper)
        old_lower = float(self._ram.generatorLower)
        new_upper = price
        new_lower = new_upper - band
        self._ram.generatorUpper = new_upper
        self._ram.generatorLower = new_lower
        levels = self._rebuild_levels()
        self._retarget_idle_tp_after_band_shift(levels)
        self._audit(
            GRID_SHIFT,
            details={
                "generatorUpper_before": old_upper,
                "generatorLower_before": old_lower,
                "generatorUpper_after": float(new_upper),
                "generatorLower_after": float(new_lower),
                "lift_trigger_price": float(price),
                "lift_above_offset": float(self._lift_above_offset),
                "generatorCount": int(self._ram.generatorCount),
            },
        )
        _log.info("grid_shift new_upper=%s new_lower=%s", new_upper, new_lower)
        return True

    def _retarget_idle_tp_after_band_shift(self, levels: list[float]) -> None:
        """Shift upper/lower together; keep lock_profit / trailing peaks absolute (no reset)."""
        tick = self._filters.get("tick_size", 0)
        for idx, st in self._ram.line_trail.items():
            if st.phase != LineTrailPhase.idle:
                continue
            if idx >= len(levels):
                continue
            lv = quantize_price(float(levels[idx]), tick) if tick else float(levels[idx])
            if self._first_side == Side.BUY:
                st.tp_level = lv + self._ram.trailingOffset
            else:
                st.tp_level = lv - self._ram.trailingOffset

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
                self._audit(
                    TRAILING_STARTED,
                    details={
                        "line_index": int(idx),
                        "trail_reference_price": float(st.lock_floor),
                        "trailingOffset": float(self._ram.trailingOffset),
                        "trail_peak_initial": float(price),
                        "trailing_stop_pct": float(self._trailing_stop_pct),
                    },
                )
                _log.debug("line %s trailing armed peak=%s", idx, st.trail_peak)
            elif st.phase == LineTrailPhase.trailing:
                if self._first_side == Side.BUY and price > st.trail_peak:
                    st.trail_peak = price
                elif self._first_side == Side.SELL and price < st.trail_peak:
                    st.trail_peak = price

    def _consume_trailing_exits(self, price: float) -> list[tuple[int, dict[str, Any]]]:
        """Detect trailing breach; return ``(line_index, exit_snapshot)`` for MARKET exits."""
        fired: list[tuple[int, dict[str, Any]]] = []
        for idx, st in list(self._ram.line_trail.items()):
            if idx in self._line_exit_mutex:
                continue
            if st.phase != LineTrailPhase.trailing or st.trail_peak <= 0:
                continue
            if self._first_side == Side.BUY:
                thr = st.trail_peak * (1.0 - self._trailing_stop_pct)
                if price < thr:
                    snap = {
                        "line_index": int(idx),
                        "trail_peak": float(st.trail_peak),
                        "trail_stop_pct": float(self._trailing_stop_pct),
                        "stop_threshold_price": float(thr),
                        "mark_at_breach": float(price),
                    }
                    fired.append((idx, snap))
                    st.phase = LineTrailPhase.idle
                    st.trail_peak = 0.0
                    st.lock_floor = 0.0
            else:
                thr = st.trail_peak * (1.0 + self._trailing_stop_pct)
                if price > thr:
                    snap = {
                        "line_index": int(idx),
                        "trail_peak": float(st.trail_peak),
                        "trail_stop_pct": float(self._trailing_stop_pct),
                        "stop_threshold_price": float(thr),
                        "mark_at_breach": float(price),
                    }
                    fired.append((idx, snap))
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
                "symbol": self._symbol,
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
                "maxGeneratorCount": self._max_generator_count,
                "hybridLineCap": self._hybrid_line_cap,
                "virtualGrid": (
                    self._virtual_book.to_snapshot_rows() if self._virtual_book else []
                ),
                "virtualExecutions": (
                    int(self._virtual_book.executions) if self._virtual_book else 0
                ),
                "lineTrail": self.line_trail_snapshot(),
                "dcaMode": self._dca_mode,
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
        """
        Grid bootstrap بحساب صارم وفق LOT_SIZE و PRICE_FILTER (عبر normalize_order):
        - لا توزيع نسبي جزئي: إذا تعذّر تأمين USDT وفق مجموع الأسعار×الكميات والشراء الآني، يُجهض التشغيل.
        - عمولة شبكية تقديرها ALKARRAR_SPOT_TAKER_FEE_RATIO (افتراض 0.1%) على أصل الأساس المستلم
          عند الشراء؛ كمية الماركت تُكمَّم لتغطية مجموع أساس أوامر البيع بعد العمولة.
        - بعد MARKET BUY يُنتظر حتى يرتفع الرصيد الحر للأساس بمقدار يغطي مجمل الكميات المطلوبة
          ثم تُسجَّل خطوط الشبكة افتراضياً (لا LIMIT على المنصة) — التنفيذ عند تقاطع Mark.
        """
        if not isinstance(self._exchange, BinanceSpotClient):
            return

        filt = dict(self._filters)
        fee_rate = _bootstrap_taker_buy_fee_decimal()
        tick_sz = float(filt.get("tick_size") or 0)

        mark = 0.0
        free_usdt_f = 0.0
        base_before_dec = Decimal(0)

        try:
            tick = await self._exchange.fetch_ticker(self._symbol)
            for k in ("price", "lastPrice", "last"):
                try:
                    mark = float(tick.get(k) or 0)
                except (TypeError, ValueError):
                    mark = 0.0
                if mark > 0:
                    break
            acc0 = await self._exchange.fetch_account()
            bal0 = parse_spot_balances(acc0)
            free_usdt_f = float(bal0.get("availableBalance") or 0)
            base_before_dec = Decimal(str(self._exchange.base_asset_free(acc0, self._symbol)))
        except Exception:
            self._audit(
                SYSTEM_ERROR,
                details={
                    "context": "bootstrap_prefetch_failed",
                    "symbol": self._symbol,
                },
            )
            _log.warning("bootstrap: prefetch failed", exc_info=True)
            return

        if mark > 0:
            self._ram.last_price = mark

        levels = self._rebuild_levels()
        qty_eff = self._effective_order_qty()
        mid = (self._ram.generatorLower + self._ram.generatorUpper) / 2.0
        eps = max(tick_sz * 0.5, mark * 1e-12, 1e-12) if mark > 0 else max(tick_sz * 0.5, 1e-12)

        level_rows: list[tuple[int, float]] = []
        for i, px in enumerate(levels):
            if self._ram.boundary_mode and px < mid:
                continue
            level_rows.append((i, px))

        upper: list[tuple[int, float]] = []
        lower: list[tuple[int, float]] = []
        neutral: list[tuple[int, float]] = []
        if mark > 0:
            for row in level_rows:
                idx, px = row
                if px > mark + eps:
                    upper.append(row)
                elif px < mark - eps:
                    lower.append(row)
                else:
                    neutral.append(row)
        else:
            neutral = list(level_rows)

        buy_levels = lower + neutral
        sell_levels = upper

        plan_buys: list[tuple[int, float, str, str]] = []
        for idx, px in buy_levels:
            try:
                p_s, q_s = _normalize_pair_exact(px, qty_eff, filt)
            except Exception as exc:
                self._audit(
                    SYSTEM_ERROR,
                    details={
                        "context": "bootstrap_normalize_buy",
                        "line_index": idx,
                        "price": px,
                        "error": str(exc)[:4096],
                    },
                )
                return
            if float(q_s) <= 0 or float(p_s) <= 0:
                self._audit(
                    SYSTEM_ERROR,
                    details={
                        "context": "bootstrap_invalid_buy_row",
                        "line_index": idx,
                        "price": px,
                    },
                )
                return
            plan_buys.append((idx, px, p_s, q_s))

        plan_sells: list[tuple[int, float, str, str]] = []
        for idx, px in sell_levels:
            try:
                p_s, q_s = _normalize_pair_exact(px, qty_eff, filt)
            except Exception as exc:
                self._audit(
                    SYSTEM_ERROR,
                    details={
                        "context": "bootstrap_normalize_sell",
                        "line_index": idx,
                        "price": px,
                        "error": str(exc)[:4096],
                    },
                )
                return
            if float(q_s) <= 0 or float(p_s) <= 0:
                self._audit(
                    SYSTEM_ERROR,
                    details={
                        "context": "bootstrap_invalid_sell_row",
                        "line_index": idx,
                        "price": px,
                    },
                )
                return
            plan_sells.append((idx, px, p_s, q_s))

        buy_quote_exact = sum(_dec_str(p) * _dec_str(q) for _, _, p, q in plan_buys)
        sell_base_need = sum(_dec_str(q) for _, _, _, q in plan_sells)

        market_qty_str: str | None = None
        reserve_market_quote = Decimal(0)
        solver_note = ""

        if sell_levels and mark > 0:
            mq, solver_note = market_buy_quantity_string_covers_net_base(
                mark,
                filters=filt,
                net_base_need=sell_base_need,
                fee_take_from_received_base=fee_rate,
            )
            if mq is None:
                self._audit(
                    SYSTEM_ERROR,
                    details={
                        "context": "bootstrap_market_buy_unsolvable",
                        "solver": solver_note,
                        "sell_base_need_exact": str(sell_base_need),
                        "fee_rate": str(fee_rate),
                        "sell_rungs": len(plan_sells),
                    },
                )
                _log.warning("bootstrap MARKET qty unsolvable: %s", solver_note)
                return
            market_qty_str = mq
            p_mk_s, q_mk_check = _normalize_pair_exact(mark, float(_dec_str(mq)), filt)
            if q_mk_check != mq:
                self._audit(
                    SYSTEM_ERROR,
                    details={
                        "context": "bootstrap_market_qty_roundtrip_mismatch",
                        "qty_expected": mq,
                        "qty_re_rounded": q_mk_check,
                        "solver": solver_note,
                    },
                )
                return
            reserve_market_quote = _dec_str(p_mk_s) * _dec_str(mq)

        reserve_total_exact = buy_quote_exact + reserve_market_quote
        wallet_usdt_dec = Decimal(str(free_usdt_f))
        ic_cap = Decimal(str(self._ram.initialCapital))

        if reserve_total_exact > wallet_usdt_dec:
            self._audit(
                SYSTEM_ERROR,
                details={
                    "context": "bootstrap_abort_wallet_insufficient",
                    "required_quote_exact": str(reserve_total_exact),
                    "free_usdt": str(wallet_usdt_dec),
                    "buy_quote_exact": str(buy_quote_exact),
                    "market_quote_exact": str(reserve_market_quote),
                    "sell_base_need_exact": str(sell_base_need),
                },
            )
            _log.warning("bootstrap abort: wallet USDT insufficient (exact)")
            return

        if reserve_total_exact > ic_cap:
            self._audit(
                SYSTEM_ERROR,
                details={
                    "context": "bootstrap_abort_initial_capital_exceeded",
                    "required_quote_exact": str(reserve_total_exact),
                    "initialCapital": str(ic_cap),
                    "buy_quote_exact": str(buy_quote_exact),
                    "market_quote_exact": str(reserve_market_quote),
                },
            )
            _log.warning("bootstrap abort: initialCapital ceiling exceeded")
            return

        if sell_levels and mark > 0 and market_qty_str:
            qty_market_final_s = market_qty_str
            try:
                res = await self._exchange.create_order(
                    symbol=self._symbol,
                    side="BUY",
                    order_type="MARKET",
                    quantity=qty_market_final_s,
                )
                try:
                    from backend.api.grid_manager import grid_manager

                    grid_manager.note_order_placed(self._symbol)
                    from backend.api.bot_hub import hub

                    await hub.broadcast_room(
                        self._symbol,
                        {
                            "type": "order",
                            "data": {
                                "symbol": self._symbol,
                                "side": "BUY",
                                "price": mark,
                                "quantity": qty_market_final_s,
                                "orderId": res.get("orderId"),
                                "status": str(res.get("status", "") or "FILLED"),
                                "order_type": "MARKET",
                                "bootstrap": True,
                            },
                        }
                    )
                except Exception:
                    pass

                bid = self._bot_id or "default"
                try:
                    from backend.api import spot_account_sync

                    rst = getattr(spot_account_sync, "reset_sync_dedupe", None)
                    if callable(rst):
                        rst()
                    await spot_account_sync.sync_spot_account_to_hub_once(bid)
                except Exception:
                    _log.debug("bootstrap post-market sync skipped", exc_info=True)

                fb_now, pole = await _await_free_base_raise(
                    self._exchange,
                    self._symbol,
                    base_before_dec,
                    sell_base_need,
                    timeout_s=30.0,
                    poll_s=0.25,
                )
                if pole != "ok" or fb_now is None:
                    self._audit(
                        SYSTEM_ERROR,
                        details={
                            "context": "bootstrap_post_buy_balance_not_confirmed",
                            "poll_result": pole,
                            "baseline_base": str(base_before_dec),
                            "required_gain": str(sell_base_need),
                            "last_free_base": str(fb_now) if fb_now is not None else "",
                            "market_qty": qty_market_final_s,
                            "orderId": res.get("orderId"),
                        },
                    )
                    _log.error("bootstrap: balance did not rise as required after MARKET BUY")
                    return

                avail_gain = fb_now - base_before_dec
                if avail_gain < sell_base_need:
                    self._audit(
                        SYSTEM_ERROR,
                        details={
                            "context": "bootstrap_insufficient_net_base_after_buy",
                            "avail_gain": str(avail_gain),
                            "need": str(sell_base_need),
                            "solver": solver_note,
                        },
                    )
                    _log.error("bootstrap: insufficient base gain vs sell ladder sums")
                    return

            except Exception as exc:
                self._audit(
                    SYSTEM_ERROR,
                    details={
                        "context": "bootstrap_market_buy_failed",
                        "error": str(exc)[:4096],
                        "qty": str(qty_market_final_s),
                    },
                )
                _log.exception("bootstrap market buy failed")
                return

        await self._arm_virtual_plan(plan_sells, Side.SELL, qty_eff)
        await self._arm_virtual_plan(plan_buys, Side.BUY, qty_eff)

    async def _arm_virtual_plan(
        self,
        plan: list[tuple[int, float, str, str]],
        side: Side,
        qty_eff: float,
    ) -> None:
        if not self._virtual_book:
            return
        lo = self._ram.generatorLower
        hi = self._ram.generatorUpper
        span = max(hi - lo, 1e-12)
        for idx, px, p_s, q_s in plan:
            bucket: Literal["all", "lower", "upper"] = "all"
            if abs(px - lo) <= span * 0.08:
                bucket = "lower"
            elif abs(px - hi) <= span * 0.08:
                bucket = "upper"
            self._virtual_book.register(
                line_index=idx,
                price=px,
                price_s=p_s,
                qty_s=q_s,
                side=side,
                bucket=bucket,
            )
            try:
                from backend.api.grid_manager import grid_manager

                grid_manager.note_order_placed(self._symbol)
            except Exception:
                pass
        self._audit(
            "VIRTUAL_GRID_ARMED",
            details={
                "side": side.value.upper(),
                "lines": len(plan),
                "qty_per_line": float(qty_eff),
                "armed_total": self._virtual_book.armed_count(),
            },
        )

    def _virtual_crossed(self, prev_mark: float, mark: float) -> list[VirtualGridLine]:
        if not self._virtual_book or not self._running:
            return []
        return self._virtual_book.crossed_lines(prev_mark, mark, first_side=self._first_side)

    async def _execute_virtual_line(self, line: VirtualGridLine, *, mark: float) -> None:
        if (
            not self._running
            or not isinstance(self._exchange, BinanceSpotClient)
            or not self._virtual_book
        ):
            return
        if line.triggered or not line.armed or line.line_index in self._line_exit_mutex:
            return
        if not self._virtual_book.throttle.allow():
            _log.debug("virtual grid throttle skip line=%s", line.line_index)
            return
        if not self._virtual_book.slippage_ok(line, mark):
            self._audit(
                SYSTEM_ERROR,
                details={
                    "context": "virtual_grid_slippage",
                    "line_index": line.line_index,
                    "line_price": line.price,
                    "mark": float(mark),
                    "max_slippage_pct": self._virtual_book.max_slippage_pct,
                },
            )
            return
        line.triggered = True
        line.armed = False
        side_u = line.side.value.upper()
        try:
            res = await self._spot_execute(
                side=line.side,
                qty=self._effective_order_qty(),
                limit_price=line.price,
                context="virtual_grid_fill",
                audit_type="VIRTUAL_GRID_FILL",
                audit_extra={
                    "line_index": line.line_index,
                    "line_price": line.price,
                    "mark": float(mark),
                },
            )
            if res is None:
                line.triggered = False
                line.armed = True
                return
            self._virtual_book.executions += 1
            try:
                from backend.api.grid_manager import grid_manager

                grid_manager.note_order_placed(self._symbol)
            except Exception:
                pass
            try:
                from backend.api.bot_hub import hub

                await hub.broadcast_room(
                    self._symbol,
                    {
                        "type": "order",
                        "data": {
                            "symbol": self._symbol,
                            "side": side_u,
                            "price": line.price,
                            "quantity": float(line.qty_s),
                            "orderId": res.get("orderId"),
                            "status": res.get("status", "FILLED"),
                            "virtual": True,
                        },
                    },
                )
            except Exception:
                pass
        except Exception as exc:
            line.triggered = False
            line.armed = True
            _log.exception("virtual grid fill failed line=%s", line.line_index)
            self._audit(
                SYSTEM_ERROR,
                details={
                    "context": "virtual_grid_fill",
                    "line_index": line.line_index,
                    "error": str(exc)[:4096],
                },
            )

    async def _io_dynamic_lift(self) -> None:
        """After band lift: disarm stale lower virtual lines and re-arm ladder at new band."""
        if not isinstance(self._exchange, BinanceSpotClient):
            return
        if self._virtual_book:
            lo = self._ram.generatorLower
            hi = self._ram.generatorUpper
            span = max(hi - lo, 1e-12)
            self._virtual_book.disarm_bucket("lower", lo=lo, hi=hi, span=span)
            self._rearm_virtual_ladder(reason="grid_lift", mode="full")
        else:
            for oid in list(self._ram.open_orders_lower):
                asyncio.create_task(self._safe_cancel(oid), name=f"cx-{oid}")
        self._ram.open_orders_lower.clear()

    async def _spot_execute(
        self,
        *,
        side: Side,
        qty: float,
        limit_price: float,
        context: str,
        audit_type: str,
        audit_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Grid/trail execution: LIMIT+IOC by default (exact price); MARKET only if env overrides."""
        if not isinstance(self._exchange, BinanceSpotClient):
            return None
        style = (
            self._virtual_book.order_style
            if self._virtual_book
            else grid_exec_settings()["order_style"]
        )
        px = float(limit_price)
        if px <= 0:
            px = float(self._ram.last_price or self._ram.generatorUpper)
        price_s, qty_s = self._normalize_order(px, qty)
        side_u = side.value.upper()
        if style == ExecOrderStyle.LIMIT_IOC:
            res = await self._exchange.create_order(
                symbol=self._symbol,
                side=side_u,
                order_type="LIMIT",
                quantity=qty_s,
                price=price_s,
                time_in_force="IOC",
            )
        else:
            res = await self._exchange.create_order(
                symbol=self._symbol,
                side=side_u,
                order_type="MARKET",
                quantity=qty_s,
            )
        est_quote = float(qty_s) * float(price_s if style == ExecOrderStyle.LIMIT_IOC else px)
        self._audit(
            audit_type,
            details={
                **(audit_extra or {}),
                "context": context,
                "orderId": res.get("orderId"),
                "side": side_u,
                "quantity": str(qty_s),
                "limit_price": float(price_s) if style == ExecOrderStyle.LIMIT_IOC else None,
                "mark_at_order": float(self._ram.last_price or 0.0),
                "order_style": style.value,
                "estimated_gross_quote_usdt": round(est_quote, 8),
            },
        )
        return res

    async def _exit_line_market(self, line_idx: int, exit_snap: dict[str, Any] | None = None) -> None:
        if not isinstance(self._exchange, BinanceSpotClient):
            return
        self._line_exit_mutex.add(line_idx)
        qty = self._effective_order_qty()
        side = Side.SELL if self._first_side == Side.BUY else Side.BUY
        snap = dict(exit_snap or {})
        try:
            exit_px = float(snap.get("stop_threshold_price") or 0.0)
            if exit_px <= 0:
                exit_px = float(snap.get("trail_peak") or self._ram.last_price or 0.0)
            await self._spot_execute(
                side=side,
                qty=qty,
                limit_price=exit_px,
                context="take_profit_trailing_exit",
                audit_type=TAKE_PROFIT_MARKET,
                audit_extra={
                    **snap,
                    "line_index": line_idx,
                    "note": "LIMIT_IOC at stop threshold when ALKARRAR_GRID_EXEC_ORDER_TYPE=LIMIT_IOC (default).",
                },
            )
        except Exception as exc:
            _log.exception("trail exit failed line=%s", line_idx)
            self._audit(
                SYSTEM_ERROR,
                details={
                    "context": "take_profit_market_exit",
                    "line_index": line_idx,
                    **snap,
                    "error": str(exc)[:4096],
                },
            )
        finally:
            self._line_exit_mutex.discard(line_idx)

    async def _place_and_track(
        self,
        price: float,
        side: Side,
        qty: float,
        bucket: Literal["all", "lower", "upper"],
        line_index: int | None = None,
    ) -> None:
        """Register a virtual grid line (no exchange LIMIT — keeps balance free)."""
        if not self._running or line_index is None:
            return
        if line_index in self._line_exit_mutex:
            return
        if not self._virtual_book:
            return
        try:
            price_s, qty_s = self._normalize_order(price, qty)
        except Exception as exc:
            _log.debug("normalize virtual line failed", exc_info=True)
            self._audit(
                SYSTEM_ERROR,
                details={
                    "context": "virtual_line_normalize",
                    "price": price,
                    "qty": qty,
                    "line_index": line_index,
                    "error": str(exc)[:4096],
                },
            )
            return
        if float(qty_s) <= 0 or float(price_s) <= 0:
            return
        self._virtual_book.register(
            line_index=line_index,
            price=price,
            price_s=price_s,
            qty_s=qty_s,
            side=side,
            bucket=bucket,
        )
        try:
            from backend.api.grid_manager import grid_manager

            grid_manager.note_order_placed(self._symbol)
        except Exception:
            pass

    async def _safe_cancel(self, order_id: int) -> None:
        if not isinstance(self._exchange, BinanceSpotClient):
            return
        try:
            await self._exchange.cancel_order(symbol=self._symbol, order_id=order_id)
        except Exception as exc:
            self._audit(
                SYSTEM_ERROR,
                details={
                    "context": "cancel_order",
                    "order_id": order_id,
                    "error": str(exc)[:4096],
                },
            )
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
                        INSERT INTO shifting_grid_snapshots (bot_id, symbol, payload, updated_ms)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(bot_id, symbol) DO UPDATE SET
                          payload = excluded.payload,
                          updated_ms = excluded.updated_ms
                        """,
                        (self._bot_id, self._symbol, item, int(time.time() * 1000)),
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
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shifting_grid_snapshots'"
        )
        exists = await cur.fetchone()
        if not exists:
            await db.execute(
                """
                CREATE TABLE shifting_grid_snapshots (
                  bot_id TEXT NOT NULL,
                  symbol TEXT NOT NULL,
                  payload TEXT NOT NULL,
                  updated_ms INTEGER NOT NULL,
                  PRIMARY KEY (bot_id, symbol)
                )
                """
            )
            await db.commit()
            return

        cur = await db.execute("PRAGMA table_info(shifting_grid_snapshots)")
        colnames = {row[1] for row in await cur.fetchall()}
        if "symbol" in colnames:
            await db.commit()
            return

        await db.execute("ALTER TABLE shifting_grid_snapshots RENAME TO shifting_grid_snapshots_old")
        await db.execute(
            """
            CREATE TABLE shifting_grid_snapshots (
              bot_id TEXT NOT NULL,
              symbol TEXT NOT NULL,
              payload TEXT NOT NULL,
              updated_ms INTEGER NOT NULL,
              PRIMARY KEY (bot_id, symbol)
            )
            """
        )
        cur = await db.execute("SELECT bot_id, payload, updated_ms FROM shifting_grid_snapshots_old")
        rows = await cur.fetchall()
        for bid, payload, updated_ms in rows:
            sym = "LEGACY"
            try:
                j = json.loads(payload)
                if isinstance(j, dict) and j.get("symbol"):
                    sym = str(j["symbol"]).strip().upper().replace("/", "")
            except Exception:
                pass
            await db.execute(
                "INSERT INTO shifting_grid_snapshots (bot_id, symbol, payload, updated_ms) "
                "VALUES (?, ?, ?, ?)",
                (bid, sym, payload, updated_ms),
            )
        await db.execute("DROP TABLE shifting_grid_snapshots_old")
        await db.commit()
