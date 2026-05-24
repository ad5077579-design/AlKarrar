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

from backend.core.exchange_filters import (
    fetch_symbol_filters,
    format_base_qty_from_usdt_slice,
    normalize_order,
    quantize_price,
)
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
    SYSTEM_ERROR,
    TAKE_PROFIT_MARKET,
    TRAILING_STARTED,
    schedule_bot_audit_event,
)
from backend.api.volatility_band import (
    VOL_BAND_RECALIBRATE,
    AvbConfig,
    band_span_pct,
    effective_band_span_pct,
    effective_lift_offset,
    effective_trailing_stop_pct,
    fetch_vol_profile,
    min_edge_spacing_pct,
    spacing_passes_fee_gate,
    span_change_pct_enough,
    try_band_for_vol_recalibrate,
)

_log = logging.getLogger(__name__)

# عمولة شبكة بينانس Spot الافتراضية (ماركت/تيكر) — تقدير محافظ لتغطية العمولة المقتطعة من أصل الأساس بعد الشراء.
# يمكن تجاوزها بـ ALKARRAR_SPOT_TAKER_FEE_RATIO=0.001
def _bootstrap_market_buy_enabled() -> bool:
    raw = str(os.getenv("ALKARRAR_GRID_BOOTSTRAP_MARKET", "0")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _bootstrap_market_max_quote_fraction() -> Decimal:
    try:
        return Decimal(str(os.getenv("ALKARRAR_GRID_BOOTSTRAP_MAX_DEPLOY_FRAC", "0.35")))
    except Exception:
        return Decimal("0.35")


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
    trailing_audit_done: bool = False
    exchange_fill_confirmed: bool = False


def spot_order_filled(res: dict[str, Any] | None) -> bool:
    """True only when Binance reports ``status: FILLED`` with executed quantity."""
    if not res:
        return False
    if str(res.get("status") or "").upper() != "FILLED":
        return False
    try:
        executed = float(res.get("executedQty") or 0)
    except (TypeError, ValueError):
        executed = 0.0
    return executed > 0


def executed_qty_from_response(res: dict[str, Any]) -> float:
    try:
        return max(float(res.get("executedQty") or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


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
    cumulative_realized_at_last_resize: float = 0.0
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
        self._boundary_reinvest_frac: float = 0.25
        self._lot_expand_step_pct: float = 0.05
        self._last_available_usdt: float = 0.0
        self._compound_resize_pct: float = 0.01
        self._upper_sell_armed_count: int = 0
        self._upper_sell_completed: int = 0
        self._asymmetric_shift_active: bool = False
        self._line_exit_mutex: set[int] = set()
        self._line_fill_mutex: set[int] = set()
        self._filled_order_ids: dict[str, int] = {}
        self._last_memory_prune_mono: float = 0.0
        self._persisted_grid_settings: dict[str, Any] = {}
        self._session_start_ms_persisted: int = 0
        self._db_path = data_dir() / "trader.db"
        self._db_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1)
        self._db_worker: asyncio.Task[None] | None = None
        self._filters: dict[str, float] = {}
        self._virtual_book: VirtualGridBook | None = None
        self._prev_mark: float = 0.0
        self._dca_mode: DcaMode = "equal"
        self._allocated_capital_usdt: float = 0.0
        self._session_base_inventory: float = 0.0
        self._lines_with_confirmed_buy: set[int] = set()
        self._line_ioc_cooldown_until: dict[int, float] = {}
        self._avb_cfg = AvbConfig.from_env()
        self._avb_enabled = False
        self._avb_base_band_span_pct = 7.0
        self._avb_base_trailing_stop_pct = 0.01
        self._avb_base_lift_offset = 0.0
        self._avb_last_recal_mono = 0.0
        self._avb_io_busy = False
        self._avb_last_atr_pct = 0.0
        self._avb_effective_span_pct = 0.0
        self._avb_min_edge_spacing_pct = 0.0

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

        self._filters = await fetch_symbol_filters(self._exchange, self._symbol)
        upper = quantize_price(upper, self._filters.get("tick_size", 0))
        lower = quantize_price(lower, self._filters.get("tick_size", 0))
        if not (lower < upper):
            raise ValueError("quantized band invalid for symbol filters")

        ic = float(settings.get("allocatedCapital") or settings.get("initialCapital") or 0.0)
        if ic <= 0:
            raise ValueError("allocatedCapital must be > 0")
        self._allocated_capital_usdt = ic
        trail_off = float(settings["trailingOffset"])
        comp = float(settings.get("compoundingFactor") or 1.0)

        band = GeneratorBand(generatorUpper=upper, generatorLower=lower, generatorCount=count)
        levels = build_dca_levels(band, mode=self._dca_mode)
        tick = self._filters.get("tick_size", 0)
        levels = [quantize_price(lv, tick) for lv in levels]

        deploy_usdt = ic
        self._last_available_usdt = deploy_usdt
        mark_px = 0.0
        try:
            tick = await self._exchange.fetch_ticker(self._symbol)
            for k in ("price", "lastPrice", "last"):
                try:
                    mark_px = float(tick.get(k) or 0)
                except (TypeError, ValueError):
                    mark_px = 0.0
                if mark_px > 0:
                    break
        except Exception:
            mark_px = 0.0
        mid = (upper + lower) / 2.0
        sizing_mark = mark_px if mark_px > 0 else mid
        qty_base = 0.0
        if deploy_usdt > 0 and sizing_mark > 0:
            qty_base, _ = format_base_qty_from_usdt_slice(
                usdt_per_line=deploy_usdt / max(count, 1),
                mark=sizing_mark,
                filters=self._filters,
            )

        self._ram = ShiftingGridRAM(
            generatorUpper=upper,
            generatorLower=lower,
            generatorCount=count,
            initialCapital=deploy_usdt,
            trailingOffset=trail_off,
            compoundingFactor=comp,
            order_quantity_base=qty_base,
            order_quantity_effective=qty_base,
        )
        self._init_line_trail_states(levels)

        default_lift = max(upper * 1.0e-4, (upper - lower) * 0.02)
        self._lift_above_offset = float(settings.get("lift_above_offset", default_lift))
        self._trailing_stop_pct = float(settings.get("trailing_stop_pct", 0.01))
        self._avb_cfg = AvbConfig.from_env(settings=settings)
        self._avb_enabled = bool(self._avb_cfg.enabled)
        mid_band = (upper + lower) / 2.0
        self._avb_base_band_span_pct = (
            float(settings.get("avbBaseBandSpanPct"))
            if settings.get("avbBaseBandSpanPct") is not None
            else (band_span_pct(upper, lower) if mid_band > 0 else self._avb_cfg.base_band_span_pct)
        )
        self._avb_base_trailing_stop_pct = float(self._trailing_stop_pct)
        self._avb_base_lift_offset = float(self._lift_above_offset)
        self._avb_effective_span_pct = float(self._avb_base_band_span_pct)
        self._avb_min_edge_spacing_pct = min_edge_spacing_pct(
            min_profit_margin=self._avb_cfg.min_profit_margin_pct,
        )
        self._avb_last_recal_mono = 0.0
        self._avb_io_busy = False
        self._avb_last_atr_pct = 0.0
        self._boundary_epsilon_pct = float(settings.get("boundary_epsilon_pct", 0.0005))
        self._boundary_reinvest_frac = float(settings.get("boundary_reinvest_frac", 0.25))
        self._lot_expand_step_pct = float(settings.get("lot_expand_step_pct", 0.05))
        self._compound_resize_pct = _compound_resize_pct_from_env(settings)
        self._upper_sell_armed_count = 0
        self._upper_sell_completed = 0
        self._asymmetric_shift_active = False
        self._session_base_inventory = 0.0
        self._lines_with_confirmed_buy = set()
        self._line_ioc_cooldown_until = {}
        self._persisted_grid_settings = dict(settings)
        self._binance_env = str(settings.get("binanceEnv") or "")
        self._credentials_fingerprint = str(settings.get("credentialsFingerprint") or "")
        resume_snap = settings.get("resumeFromSnapshot")
        if isinstance(resume_snap, dict):
            self._session_start_ms_persisted = int(
                settings.get("resumeSessionStartMs") or resume_snap.get("sessionStartMs") or 0
            )
        else:
            self._session_start_ms_persisted = int(time.time() * 1000)

        if mark_px > 0 and not isinstance(resume_snap, dict):
            from backend.api.spot_realized_ledger import validate_band_matches_symbol_mark

            validate_band_matches_symbol_mark(
                generator_upper=upper,
                generator_lower=lower,
                mark_price=mark_px,
                symbol=self._symbol,
            )

        await _ensure_shifting_grid_table(self._db_path)
        self._db_worker = asyncio.create_task(self._db_worker_loop(), name="shifting-grid-db")

        if isinstance(resume_snap, dict):
            self._restore_from_snapshot(resume_snap)
            asyncio.create_task(self._finalize_resume_after_restore(), name="shifting-grid-resume")
        else:
            asyncio.create_task(self._bootstrap_open_grid(), name="shifting-grid-bootstrap")

        if self._avb_enabled and mark_px > 0:
            self._avb_last_recal_mono = 0.0
            asyncio.create_task(self._io_avb_recalibrate(mark_px), name="avb-initial")

    async def on_stop(self, bot_id: str) -> None:
        self._running = False
        self._asymmetric_shift_active = False
        self._session_base_inventory = 0.0
        self._lines_with_confirmed_buy = set()
        self._line_ioc_cooldown_until = {}
        self._avb_io_busy = False
        self._line_fill_mutex.clear()
        self._filled_order_ids.clear()
        self._persisted_grid_settings = {}
        self._session_start_ms_persisted = 0
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
                    self._ram.profit_bank_usdt += d * self._boundary_reinvest_frac

        deploy_raw = market.get("deploy_usdt")
        if deploy_raw is None:
            deploy_raw = market.get("available_usdt")
        if deploy_raw is not None:
            try:
                self._last_available_usdt = max(float(deploy_raw), 0.0)
            except (TypeError, ValueError):
                pass

        prev_mark = self._prev_mark if self._prev_mark > 0 else price
        self._ram.last_price = price

        self._maybe_prune_stale_memory()
        self._boundary_eval(price)
        if self._should_auto_recenter_on_price(price):
            if self._recenter_grid_on_pivot(price, reason="price_breakout"):
                asyncio.create_task(self._io_dynamic_lift(), name="shifting-lift-io")

        self._maybe_schedule_avb_recalibrate(price)
        self._trailing_eval(price)
        for idx, snap in self._consume_trailing_exits(price):
            asyncio.create_task(self._exit_line_market(idx, snap), name=f"trail-exit-{idx}")

        crossed = self._virtual_crossed(prev_mark, price)
        for ln in crossed[:1]:
            if ln.line_index in self._line_fill_mutex or ln.triggered or not ln.armed:
                continue
            self._line_fill_mutex.add(ln.line_index)
            ln.armed = False
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
            self._ram.line_trail[i] = LineTrailState(
                phase=LineTrailPhase.idle,
                tp_level=tp,
                lock_floor=0.0,
                trail_peak=0.0,
                trailing_audit_done=False,
                exchange_fill_confirmed=False,
            )

    def _sync_line_trail_to_levels(self, levels: list[float]) -> None:
        """Keep line_trail indices aligned with generatorCount (prune extras, add missing)."""
        n = len(levels)
        for k in list(self._ram.line_trail.keys()):
            if k >= n:
                del self._ram.line_trail[k]
        for i, lv in enumerate(levels):
            if i in self._ram.line_trail:
                continue
            tp = lv + self._ram.trailingOffset if self._first_side == Side.BUY else lv - self._ram.trailingOffset
            self._ram.line_trail[i] = LineTrailState(
                phase=LineTrailPhase.idle,
                tp_level=tp,
                lock_floor=0.0,
                trail_peak=0.0,
                trailing_audit_done=False,
                exchange_fill_confirmed=False,
            )

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
                    "exchangeFillConfirmed": bool(st.exchange_fill_confirmed),
                    "hasSessionBuy": bool(self._line_has_confirmed_buy(idx)),
                }
            )
        return rows

    def _classify_levels_by_mark(
        self,
        mark: float,
        *,
        buys_only_strict_below: bool = False,
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

        if buys_only_strict_below:
            return list(lower), []

        buy_levels = lower + neutral
        sell_levels = upper
        return buy_levels, sell_levels

    def _rearm_is_asymmetric(self, reason: str, asymmetric_buys_only: bool) -> bool:
        if asymmetric_buys_only or self._asymmetric_shift_active:
            return True
        return reason in ("grid_lift", "last_upper_sell_closed", "price_breakout")

    def _maybe_arm_paired_sell_after_buy(self, buy_line_index: int) -> None:
        """
        After auto-shift: arm the next grid rung as SELL only once its BUY filled (no pre-seeded sells).
        """
        if not self._asymmetric_shift_active or not self._virtual_book or self._first_side != Side.BUY:
            return
        levels = self._rebuild_levels()
        sell_idx = int(buy_line_index) + 1
        if sell_idx >= len(levels):
            return
        existing = self._virtual_book.lines.get(sell_idx)
        if existing is not None and (existing.triggered or existing.armed):
            return
        sell_px = float(levels[sell_idx])
        buy_px = float(levels[buy_line_index])
        if sell_px <= buy_px:
            return
        qty_eff = self._effective_order_qty()
        try:
            price_s, qty_s = self._normalize_order(sell_px, qty_eff)
        except Exception:
            return
        if float(qty_s) <= 0 or float(price_s) <= 0:
            return
        levels = self._rebuild_levels()
        if self._avb_enabled and not spacing_passes_fee_gate(
            levels,
            sell_idx,
            min_edge=self._avb_min_edge_spacing_pct,
        ):
            return
        lo = self._ram.generatorLower
        hi = self._ram.generatorUpper
        span = max(hi - lo, 1e-12)
        bucket: Literal["all", "lower", "upper"] = "all"
        if abs(sell_px - lo) <= span * 0.08:
            bucket = "lower"
        elif abs(sell_px - hi) <= span * 0.08:
            bucket = "upper"
        self._virtual_book.register(
            line_index=sell_idx,
            price=sell_px,
            price_s=price_s,
            qty_s=qty_s,
            side=Side.SELL,
            bucket=bucket,
        )
        self._audit(
            "VIRTUAL_PAIR_SELL_ARMED",
            details={
                "buy_line_index": int(buy_line_index),
                "sell_line_index": sell_idx,
                "sell_price": sell_px,
                "asymmetric_shift": True,
            },
        )

    def _rearm_virtual_ladder(
        self,
        *,
        reason: str,
        mode: RearmMode = "full",
        asymmetric_buys_only: bool = False,
    ) -> None:
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

        asymmetric = self._rearm_is_asymmetric(reason, asymmetric_buys_only)
        buy_rows, sell_rows = self._classify_levels_by_mark(
            mark,
            buys_only_strict_below=asymmetric,
        )
        if asymmetric:
            sell_rows = []
        lo = self._ram.generatorLower
        hi = self._ram.generatorUpper
        span = max(hi - lo, 1e-12)
        registered = 0

        levels_all = self._rebuild_levels()

        def _register_row(idx: int, px: float, side: Side) -> None:
            nonlocal registered
            if idx in triggered_idx:
                return
            if self._avb_enabled and not spacing_passes_fee_gate(
                levels_all,
                idx,
                min_edge=self._avb_min_edge_spacing_pct,
            ):
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
                "asymmetric_buys_only": asymmetric,
                "lines_registered": registered,
                "armed_total": self._virtual_book.armed_count(),
                "generatorCount": int(self._ram.generatorCount),
                "mark": mark,
            },
        )

    def vol_profile_snapshot(self) -> dict[str, Any]:
        return {
            "adaptiveVolBand": bool(self._avb_enabled),
            "atrPct": round(float(self._avb_last_atr_pct), 4),
            "effectiveBandSpanPct": round(float(self._avb_effective_span_pct), 4),
            "baseBandSpanPct": round(float(self._avb_base_band_span_pct), 4),
            "trailingStopPct": round(float(self._trailing_stop_pct), 6),
            "baseTrailingStopPct": round(float(self._avb_base_trailing_stop_pct), 6),
            "liftAboveOffset": round(float(self._lift_above_offset), 8),
            "minEdgeSpacingPct": round(float(self._avb_min_edge_spacing_pct) * 100.0, 4),
        }

    def _avb_has_active_trailing(self) -> bool:
        for st in self._ram.line_trail.values():
            if st.phase in (LineTrailPhase.lock_profit, LineTrailPhase.trailing):
                return True
        return False

    def _can_recalibrate_vol_band(self) -> bool:
        if not self._avb_enabled or not self._running:
            return False
        if self._avb_io_busy:
            return False
        if self._line_fill_mutex:
            return False
        if self._avb_has_active_trailing():
            return False
        return True

    def _maybe_schedule_avb_recalibrate(self, price: float) -> None:
        if not self._avb_enabled or price <= 0:
            return
        now = time.monotonic()
        if now - self._avb_last_recal_mono < max(self._avb_cfg.recal_interval_s, 60.0):
            return
        if not self._can_recalibrate_vol_band():
            return
        self._avb_last_recal_mono = now
        asyncio.create_task(self._io_avb_recalibrate(price), name="avb-vol-recal")

    def _apply_avb_trailing_params(self, atr_pct: float) -> None:
        band_w = max(float(self._ram.generatorUpper) - float(self._ram.generatorLower), 1e-12)
        self._trailing_stop_pct = effective_trailing_stop_pct(
            self._avb_base_trailing_stop_pct,
            atr_pct,
            self._avb_cfg,
        )
        self._lift_above_offset = effective_lift_offset(
            self._avb_base_lift_offset,
            band_w,
            atr_pct,
            self._avb_cfg,
        )
        self._avb_effective_span_pct = effective_band_span_pct(
            self._avb_base_band_span_pct,
            atr_pct,
            self._avb_cfg,
        )
        self._avb_min_edge_spacing_pct = min_edge_spacing_pct(
            min_profit_margin=self._avb_cfg.min_profit_margin_pct,
        )

    def _recenter_band_for_vol(self, mark: float, span_pct: float) -> bool:
        alloc = max(
            float(getattr(self, "_allocated_capital_usdt", 0.0) or self._ram.initialCapital),
            0.0,
        )
        tick = float(self._filters.get("tick_size") or 0)
        band_pair = try_band_for_vol_recalibrate(
            mark=mark,
            span_pct=span_pct,
            tick_size=tick,
            generator_count=int(self._ram.generatorCount),
            allocated_capital=alloc,
        )
        if band_pair is None:
            return False
        new_lower, new_upper = band_pair
        cur_span = band_span_pct(self._ram.generatorUpper, self._ram.generatorLower)
        if not span_change_pct_enough(cur_span, span_pct, self._avb_cfg.recal_span_change_pct):
            return False
        eps = self._upper_band_epsilon()
        if (
            abs(new_upper - self._ram.generatorUpper) < eps
            and abs(new_lower - self._ram.generatorLower) < eps
        ):
            return False

        old_upper = float(self._ram.generatorUpper)
        old_lower = float(self._ram.generatorLower)
        self._ram.generatorUpper = float(new_upper)
        self._ram.generatorLower = float(new_lower)
        levels = self._rebuild_levels()
        self._sync_line_trail_to_levels(levels)
        self._retarget_idle_tp_after_band_shift(levels)
        self._audit(
            VOL_BAND_RECALIBRATE,
            details={
                "shift_reason": "vol_recalibrate",
                "atr_pct": round(float(self._avb_last_atr_pct), 4),
                "effective_band_span_pct": round(float(span_pct), 4),
                "generatorUpper_before": old_upper,
                "generatorLower_before": old_lower,
                "generatorUpper_after": float(new_upper),
                "generatorLower_after": float(new_lower),
                "trailing_stop_pct": float(self._trailing_stop_pct),
                "lift_above_offset": float(self._lift_above_offset),
                "mark": float(mark),
            },
        )
        _log.info(
            "avb recenter span=%.3f%% upper=%s lower=%s atr=%.3f%%",
            span_pct,
            new_upper,
            new_lower,
            self._avb_last_atr_pct,
        )
        return True

    async def _io_avb_recalibrate(self, mark: float) -> None:
        if not isinstance(self._exchange, BinanceSpotClient) or not self._running:
            return
        if self._avb_io_busy:
            return
        self._avb_io_busy = True
        try:
            profile = await fetch_vol_profile(
                self._exchange,
                self._symbol,
                cfg=self._avb_cfg,
                base_band_span_pct=self._avb_base_band_span_pct,
                base_trailing_stop_pct=self._avb_base_trailing_stop_pct,
            )
            self._avb_last_atr_pct = float(profile.atr_pct)
            self._apply_avb_trailing_params(self._avb_last_atr_pct)
            mk = float(mark or self._ram.last_price or 0)
            if mk <= 0:
                return
            if not self._can_recalibrate_vol_band():
                return
            span_changed = self._recenter_band_for_vol(mk, profile.effective_band_span_pct)
            if span_changed and self._virtual_book:
                self._rearm_virtual_ladder(reason="vol_recalibrate", mode="full")
            elif self._virtual_book:
                self._rearm_virtual_ladder(reason="vol_params_only", mode="qty_only")
        except Exception as exc:
            _log.warning("avb recalibrate failed: %s", exc)
            self._audit(
                SYSTEM_ERROR,
                details={"context": "avb_recalibrate", "error": str(exc)[:500]},
            )
        finally:
            self._avb_io_busy = False

    def _reference_price(self, price: float, *, mark: float | None = None) -> float:
        """Conservative notional reference: never under-price vs live mark (prevents oversized base qty)."""
        return max(
            float(price or 0),
            float(mark or 0),
            float(self._ram.last_price or 0),
        )

    def _usdt_per_line_budget(self) -> float:
        alloc = max(
            float(getattr(self, "_allocated_capital_usdt", 0.0) or self._ram.initialCapital),
            0.0,
        )
        n = max(int(self._ram.generatorCount), 1)
        profit = max(float(self._ram.cumulative_realized_usdt), 0.0)
        deploy = alloc + profit
        return deploy / n

    def _cap_qty_to_line_budget(
        self,
        price: float,
        qty: float,
        *,
        mark: float | None = None,
    ) -> float:
        ref_px = self._reference_price(price, mark=mark)
        if ref_px <= 0 or qty <= 0:
            return 0.0
        budget = self._usdt_per_line_budget()
        if budget <= 0:
            return qty
        notional = qty * ref_px
        if notional <= budget * 1.02:
            return qty
        capped, _ = format_base_qty_from_usdt_slice(
            usdt_per_line=budget,
            mark=ref_px,
            filters=self._filters,
        )
        return min(qty, capped) if capped > 0 else qty

    def _cap_sell_to_session_inventory(self, qty: float) -> float:
        """Ring-fence sells: only base bought in this grid session."""
        req = max(float(qty), 0.0)
        hook = getattr(self, "_ledger_cap_sell_cb", None)
        if callable(hook):
            try:
                capped = float(hook(req))
                if capped >= 0:
                    req = capped
            except Exception:
                pass
        avail = max(float(self._session_base_inventory), 0.0)
        return min(req, avail) if avail > 0 else 0.0

    def _apply_session_inventory_delta(self, side: Side, res: dict[str, Any]) -> None:
        if not spot_order_filled(res):
            return
        dq = executed_qty_from_response(res)
        if dq <= 0:
            return
        if self._first_side == Side.BUY:
            if side == Side.BUY:
                self._session_base_inventory += dq
            elif side == Side.SELL:
                self._session_base_inventory = max(self._session_base_inventory - dq, 0.0)
        else:
            if side == Side.SELL:
                self._session_base_inventory += dq
            elif side == Side.BUY:
                self._session_base_inventory = max(self._session_base_inventory - dq, 0.0)

    def _qty_for_virtual_line(self, line: VirtualGridLine, *, mark: float | None = None) -> float:
        ref_px = self._reference_price(line.price, mark=mark)
        budget = self._usdt_per_line_budget()
        slice_qty, qty_s = format_base_qty_from_usdt_slice(
            usdt_per_line=budget,
            mark=ref_px,
            filters=self._filters,
        )
        if slice_qty > 0:
            return slice_qty
        try:
            return float(qty_s)
        except (TypeError, ValueError):
            return 0.0

    def _normalize_order(self, price: float, qty: float) -> tuple[str, str]:
        ref_px = self._reference_price(price)
        qty = self._cap_qty_to_line_budget(ref_px, qty, mark=ref_px)
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
        if event_type not in ("VIRTUAL_GRID_FILL", "TAKE_PROFIT_MARKET"):
            try:
                from backend.api.grid_live_ledger import log_from_audit_event

                log_from_audit_event(
                    self,
                    event_type,
                    realized_usdt=float(realized_usdt or 0.0),
                    details=d,
                )
            except Exception:
                pass

    def effective_deploy_usdt(self) -> float:
        """Ring-fenced capital: frozen allocatedCapital + this grid's cumulative realized only."""
        alloc = max(
            float(getattr(self, "_allocated_capital_usdt", 0.0) or self._ram.initialCapital),
            0.0,
        )
        return max(alloc + float(self._ram.cumulative_realized_usdt), 0.0)

    def refresh_order_size_from_capital(
        self,
        *,
        deploy_usdt: float | None = None,
        available_usdt: float | None = None,
        mark: float,
        reason: str = "cycle",
        force: bool = False,
    ) -> bool:
        """
        Resize when cumulative realized since last resize >= compound_resize_pct * deploy.
        Order size per line = ring-fenced deploy / generatorCount (not wallet balance).
        """
        n = max(int(self._ram.generatorCount), 1)
        deploy = float(deploy_usdt if deploy_usdt is not None else available_usdt if available_usdt is not None else 0.0)
        if deploy <= 0:
            deploy = self.effective_deploy_usdt()
        deploy = min(deploy, self.effective_deploy_usdt())
        if deploy <= 0 or mark <= 0:
            return False

        new_profit = self._ram.cumulative_realized_usdt - self._ram.cumulative_realized_at_last_resize
        threshold_usdt = deploy * max(self._compound_resize_pct, 0.0)
        eligible = force or threshold_usdt <= 0 or new_profit >= threshold_usdt
        if not eligible:
            return False

        slice_usdt = deploy / n
        new_qty, qty_s = format_base_qty_from_usdt_slice(
            usdt_per_line=slice_usdt,
            mark=mark,
            filters=self._filters,
        )
        if new_qty <= 0 or qty_s == "0":
            return False

        prev_qty = float(self._ram.order_quantity_effective)
        self._ram.order_quantity_base = new_qty
        self._ram.order_quantity_effective = new_qty
        changed = abs(new_qty - prev_qty) > max(prev_qty * 1e-8, 1e-12)
        if changed:
            self._ram.injections_done += 1
            self._audit(
                PROFIT_INJECT_COMPOUND,
                realized_usdt=0.0,
                details={
                    "reason": reason,
                    "profit_injection_mode": "compound_size",
                    "compound_resize_pct": self._compound_resize_pct,
                    "new_realized_since_resize": round(new_profit, 8),
                    "resize_threshold_usdt": round(threshold_usdt, 8),
                    "deploy_usdt": round(deploy, 8),
                    "usdt_per_line": round(slice_usdt, 8),
                    "qty_formatted": qty_s,
                    "generatorCount": int(self._ram.generatorCount),
                    "order_quantity_effective_before": prev_qty,
                    "order_quantity_effective_after": float(new_qty),
                    "cumulative_realized_usdt": round(self._ram.cumulative_realized_usdt, 8),
                },
            )
            _log.info(
                "dynamic_capital_sizing reason=%s avail=%.4f count=%s qty_eff=%s",
                reason,
                deploy,
                n,
                qty_s,
            )
        if changed or eligible:
            self._ram.cumulative_realized_at_last_resize = float(self._ram.cumulative_realized_usdt)
        return changed or eligible

    def _boundary_eval(self, price: float) -> None:
        lo = self._ram.generatorLower
        eps = max(lo * self._boundary_epsilon_pct, 1e-9)
        if price <= lo + eps:
            if not self._ram.boundary_mode:
                _log.info("boundary_mode: price touched lower band")
            self._ram.boundary_mode = True
            deploy = max(self.effective_deploy_usdt(), 1e-9)
            step = self._lot_expand_step_pct * max(self._ram.profit_bank_usdt, 0.0) / deploy
            if step > 0:
                self._ram.lot_expansion_multiplier += min(step, self._lot_expand_step_pct)
                self._ram.profit_bank_usdt *= 1.0 - self._boundary_reinvest_frac

    def _upper_band_epsilon(self) -> float:
        tick = float(self._filters.get("tick_size") or 0)
        return max(tick * 0.5, 1e-12)

    def _is_price_above_generator_upper(self, price: float) -> bool:
        return float(price) > float(self._ram.generatorUpper) + self._upper_band_epsilon()

    def _upper_sell_lines_pending(self) -> int:
        """Armed SELL virtual lines strictly above generatorUpper."""
        if not self._virtual_book:
            return 0
        hi = float(self._ram.generatorUpper)
        eps = self._upper_band_epsilon()
        n = 0
        for ln in self._virtual_book.lines.values():
            if ln.side != Side.SELL or ln.triggered or not ln.armed:
                continue
            if ln.price > hi + eps:
                n += 1
        return n

    def _should_auto_recenter_on_price(self, price: float) -> bool:
        if price < self._ram.generatorUpper + self._lift_above_offset:
            return False
        if self._upper_sell_armed_count > 0:
            return (
                self._upper_sell_lines_pending() == 0
                and self._upper_sell_completed >= self._upper_sell_armed_count
            )
        return True

    def _recenter_grid_on_pivot(self, pivot: float, *, reason: str) -> bool:
        """Auto-shift: new pivot at current price, same band width and generatorCount."""
        band = float(self._ram.generatorUpper) - float(self._ram.generatorLower)
        if band <= 0 or pivot <= 0:
            return False
        tick = float(self._filters.get("tick_size") or 0)
        new_upper = quantize_price(float(pivot), tick) if tick else float(pivot)
        new_lower = quantize_price(new_upper - band, tick) if tick else new_upper - band
        if not (new_lower < new_upper):
            return False
        if abs(new_upper - self._ram.generatorUpper) < self._upper_band_epsilon() and abs(
            new_lower - self._ram.generatorLower
        ) < self._upper_band_epsilon():
            return False

        old_upper = float(self._ram.generatorUpper)
        old_lower = float(self._ram.generatorLower)
        self._ram.generatorUpper = new_upper
        self._ram.generatorLower = new_lower
        levels = self._rebuild_levels()
        self._sync_line_trail_to_levels(levels)
        self._retarget_idle_tp_after_band_shift(levels)
        self._upper_sell_armed_count = 0
        self._upper_sell_completed = 0
        self._asymmetric_shift_active = True
        self._audit(
            GRID_SHIFT,
            details={
                "shift_reason": reason,
                "generatorUpper_before": old_upper,
                "generatorLower_before": old_lower,
                "generatorUpper_after": float(new_upper),
                "generatorLower_after": float(new_lower),
                "pivot_price": float(pivot),
                "generatorCount": int(self._ram.generatorCount),
                "upper_sell_completed": int(self._upper_sell_completed),
            },
        )
        _log.info(
            "auto_shift reason=%s new_upper=%s new_lower=%s",
            reason,
            new_upper,
            new_lower,
        )
        return True

    def _lift_eval_and_mutate_ram(self, price: float) -> bool:
        """Legacy name used in tests — delegates to auto-shift recenter."""
        if price < self._ram.generatorUpper + self._lift_above_offset:
            return False
        return self._recenter_grid_on_pivot(price, reason="price_breakout")

    async def _maybe_auto_shift_after_upper_sell(self, pivot: float) -> None:
        if not self._running:
            return
        if self._upper_sell_lines_pending() > 0:
            return
        if self._upper_sell_armed_count > 0 and self._upper_sell_completed < self._upper_sell_armed_count:
            return
        if self._recenter_grid_on_pivot(pivot, reason="last_upper_sell_closed"):
            await self._io_dynamic_lift()

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

    def _line_has_confirmed_buy(self, line_index: int) -> bool:
        return int(line_index) in self._lines_with_confirmed_buy

    def _line_trail_allows_trailing(self, line_index: int, st: LineTrailState) -> bool:
        return bool(st.exchange_fill_confirmed) and self._line_has_confirmed_buy(line_index)

    def _reset_phantom_trail_state(self, line_index: int, st: LineTrailState) -> None:
        """Force idle when this line has no session BUY fill (blocks mark-only trailing)."""
        if self._line_trail_allows_trailing(line_index, st):
            return
        st.phase = LineTrailPhase.idle
        st.trail_peak = 0.0
        st.lock_floor = 0.0
        st.trailing_audit_done = False
        st.exchange_fill_confirmed = False

    def _apply_exchange_fill_to_line_trail(self, line_index: int, side: Side) -> None:
        st = self._ram.line_trail.get(line_index)
        if st is None:
            return
        if self._first_side == Side.BUY:
            if side == Side.BUY:
                self._lines_with_confirmed_buy.add(int(line_index))
                st.exchange_fill_confirmed = True
                st.phase = LineTrailPhase.idle
                st.trail_peak = 0.0
                st.lock_floor = 0.0
                st.trailing_audit_done = False
            elif side == Side.SELL:
                self._lines_with_confirmed_buy.discard(int(line_index))
                st.exchange_fill_confirmed = False
                st.phase = LineTrailPhase.idle
                st.trail_peak = 0.0
                st.lock_floor = 0.0
                st.trailing_audit_done = False
        else:
            if side == Side.SELL:
                self._lines_with_confirmed_buy.add(int(line_index))
                st.exchange_fill_confirmed = True
                st.phase = LineTrailPhase.idle
                st.trail_peak = 0.0
                st.lock_floor = 0.0
                st.trailing_audit_done = False
            elif side == Side.BUY:
                self._lines_with_confirmed_buy.discard(int(line_index))
                st.exchange_fill_confirmed = False
                st.phase = LineTrailPhase.idle
                st.trail_peak = 0.0
                st.lock_floor = 0.0
                st.trailing_audit_done = False

    def _trailing_eval(self, price: float) -> None:
        """
        TP touch: LockProfit (no immediate close). Next ticks: arm trailing stop at X% (``trailing_stop_pct``)
        off the momentum peak (``trail_peak`` ratchets with favorable movement).
        """
        for idx, st in self._ram.line_trail.items():
            self._reset_phantom_trail_state(idx, st)
            if not self._line_trail_allows_trailing(idx, st):
                continue
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
                if not st.trailing_audit_done:
                    st.trailing_audit_done = True
                    self._audit(
                        TRAILING_STARTED,
                        details={
                            "line_index": int(idx),
                            "exchange_fill_confirmed": True,
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
            self._reset_phantom_trail_state(idx, st)
            if not self._line_trail_allows_trailing(idx, st):
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
                    st.trailing_audit_done = False
                    st.exchange_fill_confirmed = False
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
                    st.trailing_audit_done = False
                    st.exchange_fill_confirmed = False
        return fired

    def _effective_order_qty(self) -> float:
        m = self._ram.lot_expansion_multiplier if self._ram.boundary_mode else 1.0
        return self._ram.order_quantity_effective * m

    def _filled_oid_retention_ms(self) -> int:
        raw = (os.getenv("ALKARRAR_FILLED_OID_RETENTION_MS") or "").strip()
        if raw:
            try:
                return max(60_000, int(raw))
            except (TypeError, ValueError):
                pass
        return 48 * 3600 * 1000

    def _record_filled_order_id(self, oid: str) -> bool:
        """Return True if this orderId was not seen in the retention window."""
        if not oid:
            return True
        now = int(time.time() * 1000)
        prev = self._filled_order_ids.get(oid)
        self._filled_order_ids[oid] = now
        if prev is not None:
            return False
        return True

    def _maybe_prune_stale_memory(self) -> None:
        now_mono = time.monotonic()
        if now_mono - self._last_memory_prune_mono < 60.0:
            return
        self._last_memory_prune_mono = now_mono
        cutoff = int(time.time() * 1000) - self._filled_oid_retention_ms()
        self._filled_order_ids = {k: v for k, v in self._filled_order_ids.items() if v >= cutoff}
        cooldown_cutoff = time.monotonic() - 3600.0
        self._line_ioc_cooldown_until = {
            k: v for k, v in self._line_ioc_cooldown_until.items() if v >= cooldown_cutoff
        }

    def build_persist_payload(self, *, auto_resume: bool | None = None) -> dict[str, Any]:
        resume_flag = self._running if auto_resume is None else bool(auto_resume)
        return {
            "snapshotVersion": 1,
            "autoResume": resume_flag,
            "binanceEnv": str(getattr(self, "_binance_env", "") or ""),
            "credentialsFingerprint": str(getattr(self, "_credentials_fingerprint", "") or ""),
            "strategy": self.name,
            "symbol": self._symbol,
            "sessionStartMs": int(self._session_start_ms_persisted or 0),
            "gridSettings": dict(self._persisted_grid_settings),
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
            "order_quantity_base": self._ram.order_quantity_base,
            "injections_done": self._ram.injections_done,
            "lastAvailableUsdt": self._last_available_usdt,
            "profitInjectionMode": "compound_size",
            "asymmetricShiftActive": bool(self._asymmetric_shift_active),
            "upperSellArmedCount": int(self._upper_sell_armed_count),
            "upperSellCompleted": int(self._upper_sell_completed),
            "linesWithConfirmedBuy": sorted(int(x) for x in self._lines_with_confirmed_buy),
            "filledOrderIds": dict(self._filled_order_ids),
            "virtualGrid": (
                self._virtual_book.to_snapshot_rows() if self._virtual_book else []
            ),
            "virtualExecutions": (
                int(self._virtual_book.executions) if self._virtual_book else 0
            ),
            "lineTrail": self.line_trail_snapshot(),
            "dcaMode": self._dca_mode,
            "lift_above_offset": float(self._lift_above_offset),
            "trailing_stop_pct": float(self._trailing_stop_pct),
            "adaptiveVolBand": bool(self._avb_enabled),
            "avbBaseBandSpanPct": float(self._avb_base_band_span_pct),
            "avbEffectiveBandSpanPct": float(self._avb_effective_span_pct),
            "avbLastAtrPct": float(self._avb_last_atr_pct),
            "avbMinEdgeSpacingPct": float(self._avb_min_edge_spacing_pct),
        }

    def _restore_from_snapshot(self, snap: dict[str, Any]) -> None:
        """Rebuild RAM + virtual ladder from last persisted state (no bootstrap MARKET)."""
        if self._virtual_book is None:
            self._virtual_book = VirtualGridBook.from_env(self._symbol)
        vrows = snap.get("virtualGrid")
        if isinstance(vrows, list) and vrows:
            self._virtual_book.load_snapshot_rows(vrows)
            self._virtual_book.executions = int(snap.get("virtualExecutions") or 0)

        self._ram.cumulative_realized_usdt = float(snap.get("cumulative_realized_usdt") or 0)
        self._ram.cumulative_realized_at_last_resize = float(
            snap.get("cumulative_realized_at_last_resize") or self._ram.cumulative_realized_usdt
        )
        self._ram.profit_bank_usdt = float(snap.get("profit_bank_usdt") or 0)
        self._ram.boundary_mode = bool(snap.get("boundary_mode"))
        self._ram.lot_expansion_multiplier = float(snap.get("lot_expansion_multiplier") or 1.0)
        self._ram.order_quantity_effective = float(
            snap.get("order_quantity_effective") or self._ram.order_quantity_effective
        )
        self._ram.order_quantity_base = float(
            snap.get("order_quantity_base") or self._ram.order_quantity_base
        )
        self._ram.injections_done = int(snap.get("injections_done") or 0)
        self._ram.last_price = float(snap.get("last_price") or 0)
        self._last_available_usdt = float(snap.get("lastAvailableUsdt") or self._last_available_usdt)
        if snap.get("adaptiveVolBand") is not None:
            self._avb_enabled = bool(snap.get("adaptiveVolBand"))
        if snap.get("avbBaseBandSpanPct") is not None:
            self._avb_base_band_span_pct = float(snap.get("avbBaseBandSpanPct"))
        if snap.get("avbEffectiveBandSpanPct") is not None:
            self._avb_effective_span_pct = float(snap.get("avbEffectiveBandSpanPct"))
        if snap.get("avbLastAtrPct") is not None:
            self._avb_last_atr_pct = float(snap.get("avbLastAtrPct"))
        if snap.get("trailing_stop_pct") is not None:
            self._trailing_stop_pct = float(snap.get("trailing_stop_pct"))
        if snap.get("lift_above_offset") is not None:
            self._lift_above_offset = float(snap.get("lift_above_offset"))
        self._asymmetric_shift_active = bool(snap.get("asymmetricShiftActive"))
        self._upper_sell_armed_count = int(snap.get("upperSellArmedCount") or 0)
        self._upper_sell_completed = int(snap.get("upperSellCompleted") or 0)
        self._lines_with_confirmed_buy = set()
        for x in snap.get("linesWithConfirmedBuy") or []:
            try:
                self._lines_with_confirmed_buy.add(int(x))
            except (TypeError, ValueError):
                continue
        oid_map = snap.get("filledOrderIds")
        if isinstance(oid_map, dict):
            self._filled_order_ids = {str(k): int(v) for k, v in oid_map.items() if k}
        elif isinstance(oid_map, list):
            now = int(time.time() * 1000)
            self._filled_order_ids = {str(o): now for o in oid_map if o}

        for row in snap.get("lineTrail") or []:
            if not isinstance(row, dict):
                continue
            idx = int(row.get("lineIndex", -1))
            if idx < 0:
                continue
            phase_raw = str(row.get("phase", "idle"))
            try:
                phase = LineTrailPhase(phase_raw)
            except ValueError:
                phase = LineTrailPhase.idle
            st = self._ram.line_trail.get(idx)
            if st is None:
                continue
            st.phase = phase
            st.tp_level = float(row.get("tpLevel") or st.tp_level)
            st.trail_peak = float(row.get("trailPeak") or 0)
            st.lock_floor = float(row.get("lockFloor") or 0)
            st.exchange_fill_confirmed = bool(row.get("exchangeFillConfirmed"))
            st.trailing_audit_done = phase == LineTrailPhase.trailing

        if self._ram.last_price > 0:
            self._prev_mark = float(self._ram.last_price)
        _log.info(
            "grid state restored symbol=%s virtual_lines=%s asymmetric=%s",
            self._symbol,
            len(self._virtual_book.lines) if self._virtual_book else 0,
            self._asymmetric_shift_active,
        )

    async def _finalize_resume_after_restore(self) -> None:
        if not self._running or not isinstance(self._exchange, BinanceSpotClient):
            return
        if self._virtual_book and not self._virtual_book.lines:
            mark = float(self._ram.last_price or 0)
            if mark <= 0:
                try:
                    tick = await self._exchange.fetch_ticker(self._symbol)
                    for k in ("price", "lastPrice", "last"):
                        try:
                            mark = float(tick.get(k) or 0)
                        except (TypeError, ValueError):
                            mark = 0.0
                        if mark > 0:
                            break
                except Exception:
                    mark = 0.0
            if mark > 0:
                self._ram.last_price = mark
                self._prev_mark = mark
            self._rearm_virtual_ladder(
                reason="resume_empty_book",
                mode="full",
                asymmetric_buys_only=bool(self._asymmetric_shift_active),
            )
        await self.persist_resume_snapshot(auto_resume=True)
        self._audit(
            "VIRTUAL_REARM",
            details={"reason": "resume_after_crash", "context": "grid_auto_resume"},
        )

    async def persist_resume_snapshot(self, *, auto_resume: bool) -> None:
        if not self._bot_id or not self._symbol:
            return
        from backend.api.grid_snapshot_store import write_snapshot_payload

        payload = self.build_persist_payload(auto_resume=auto_resume)
        await write_snapshot_payload(self._bot_id, self._symbol, payload)

    def _schedule_db_snapshot(self) -> None:
        blob = json.dumps(self.build_persist_payload(), separators=(",", ":"))
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
        deploy_dec = Decimal(str(max(self.effective_deploy_usdt(), 0.0)))

        if deploy_dec > 0 and reserve_total_exact > deploy_dec:
            if sell_levels and buy_quote_exact <= deploy_dec:
                self._audit(
                    "VIRTUAL_GRID_ARMED",
                    details={
                        "context": "bootstrap_defer_sell_ladder_within_deploy",
                        "required_quote_exact": str(reserve_total_exact),
                        "deploy_cap": str(deploy_dec),
                        "sell_rungs_deferred": len(plan_sells),
                    },
                )
                plan_sells = []
                sell_levels = []
                market_qty_str = None
                reserve_market_quote = Decimal(0)
                reserve_total_exact = buy_quote_exact
                self._asymmetric_shift_active = True
            else:
                self._audit(
                    SYSTEM_ERROR,
                    details={
                        "context": "bootstrap_abort_deploy_insufficient",
                        "required_quote_exact": str(reserve_total_exact),
                        "deploy_cap": str(deploy_dec),
                        "buy_quote_exact": str(buy_quote_exact),
                        "market_quote_exact": str(reserve_market_quote),
                    },
                )
                _log.warning("bootstrap abort: exceeds ring-fenced deploy")
                return

        if reserve_total_exact > wallet_usdt_dec:
            self._audit(
                SYSTEM_ERROR,
                details={
                    "context": "bootstrap_abort_wallet_insufficient",
                    "required_quote_exact": str(reserve_total_exact),
                    "free_usdt": str(wallet_usdt_dec),
                    "deploy_cap": str(deploy_dec),
                    "buy_quote_exact": str(buy_quote_exact),
                    "market_quote_exact": str(reserve_market_quote),
                    "sell_base_need_exact": str(sell_base_need),
                },
            )
            _log.warning("bootstrap abort: wallet USDT insufficient (exact)")
            return

        bootstrap_market = _bootstrap_market_buy_enabled()
        if sell_levels and not bootstrap_market:
            self._audit(
                "VIRTUAL_GRID_ARMED",
                details={
                    "context": "bootstrap_market_disabled_virtual_only",
                    "sell_rungs_deferred": len(plan_sells),
                },
            )
            plan_sells = []
            sell_levels = []
            market_qty_str = None
            reserve_market_quote = Decimal(0)
            self._asymmetric_shift_active = True
        elif (
            sell_levels
            and market_qty_str
            and deploy_dec > 0
            and reserve_market_quote > deploy_dec * _bootstrap_market_max_quote_fraction()
            and buy_quote_exact <= deploy_dec
        ):
            self._audit(
                "VIRTUAL_GRID_ARMED",
                details={
                    "context": "bootstrap_defer_market_buy_cap",
                    "market_quote_would_be": str(reserve_market_quote),
                    "deploy_cap": str(deploy_dec),
                    "max_frac": str(_bootstrap_market_max_quote_fraction()),
                    "sell_rungs_deferred": len(plan_sells),
                },
            )
            plan_sells = []
            sell_levels = []
            market_qty_str = None
            reserve_market_quote = Decimal(0)
            self._asymmetric_shift_active = True

        if sell_levels and mark > 0 and market_qty_str and not self._asymmetric_shift_active and bootstrap_market:
            qty_market_final_s = market_qty_str
            try:
                res = await self._exchange.create_order(
                    symbol=self._symbol,
                    side="BUY",
                    order_type="MARKET",
                    quantity=qty_market_final_s,
                )
                if not spot_order_filled(res):
                    self._audit(
                        SYSTEM_ERROR,
                        details={
                            "context": "bootstrap_market_buy_not_filled",
                            "order_status": str(res.get("status") or ""),
                            "executedQty": str(res.get("executedQty") or ""),
                            "orderId": res.get("orderId"),
                        },
                    )
                    _log.error("bootstrap: MARKET BUY not FILLED on exchange")
                    return
                self._apply_session_inventory_delta(Side.BUY, res)
                try:
                    from backend.api.grid_live_ledger import (
                        fill_price_from_order_response,
                        log_grid_order_fill,
                    )

                    fill_px = fill_price_from_order_response(res, mark)
                    log_grid_order_fill(
                        self,
                        side="BUY",
                        order_id=res.get("orderId"),
                        target_price=mark,
                        fill_price=fill_px,
                        quantity=float(res.get("executedQty") or qty_market_final_s),
                        context="bootstrap_market_buy",
                    )
                except Exception:
                    _log.debug("bootstrap ledger log skipped", exc_info=True)
                if self._virtual_book:
                    self._virtual_book.executions += 1
                cb = getattr(self, "_on_exchange_fill_cb", None)
                if callable(cb):
                    try:
                        await cb(Side.BUY, res)
                    except Exception:
                        _log.debug("bootstrap ledger hook failed", exc_info=True)
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
                                "status": str(res.get("status", "") or ""),
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

        if self._asymmetric_shift_active:
            plan_sells = []
        upper_sell_rows = [(idx, px) for idx, px, _, _ in plan_sells if self._is_price_above_generator_upper(px)]
        self._upper_sell_armed_count = len(upper_sell_rows)
        self._upper_sell_completed = 0
        if plan_sells:
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
        _log.debug(
            "virtual grid armed side=%s lines=%s total=%s",
            side.value.upper(),
            len(plan),
            self._virtual_book.armed_count(),
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
        if line.line_index in self._line_exit_mutex:
            return
        cooldown_until = self._line_ioc_cooldown_until.get(line.line_index, 0.0)
        if time.monotonic() < cooldown_until:
            line.triggered = False
            line.armed = True
            return
        if line.triggered:
            return
        if line.line_index not in self._line_fill_mutex:
            if not line.armed:
                return
            self._line_fill_mutex.add(line.line_index)
            line.armed = False
        if not self._virtual_book.throttle.allow():
            _log.debug("virtual grid throttle skip line=%s", line.line_index)
            self._line_fill_mutex.discard(line.line_index)
            line.triggered = False
            line.armed = True
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
            self._line_fill_mutex.discard(line.line_index)
            line.triggered = False
            line.armed = True
            return
        side_u = line.side.value.upper()
        qty_fill = self._qty_for_virtual_line(line, mark=mark)
        if line.side == Side.SELL:
            qty_fill = self._cap_sell_to_session_inventory(qty_fill)
            if qty_fill <= 0:
                line.triggered = False
                line.armed = True
                self._line_fill_mutex.discard(line.line_index)
                self._audit(
                    SYSTEM_ERROR,
                    details={
                        "context": "virtual_grid_sell_no_session_inventory",
                        "line_index": line.line_index,
                        "session_base_inventory": float(self._session_base_inventory),
                    },
                )
                return
        try:
            res = await self._spot_execute(
                side=line.side,
                qty=qty_fill,
                limit_price=line.price,
                context="virtual_grid_fill",
                audit_type="VIRTUAL_GRID_FILL",
                audit_extra={
                    "line_index": line.line_index,
                    "line_price": line.price,
                    "mark": float(mark),
                },
            )
            if res is None or not spot_order_filled(res):
                line.triggered = False
                line.armed = True
                self._line_fill_mutex.discard(line.line_index)
                if res is not None:
                    self._line_ioc_cooldown_until[line.line_index] = time.monotonic() + 8.0
                    _log.debug(
                        "virtual grid IOC miss line=%s status=%s",
                        line.line_index,
                        res.get("status"),
                    )
                return
            line.triggered = True
            self._apply_session_inventory_delta(line.side, res)
            self._apply_exchange_fill_to_line_trail(line.line_index, line.side)
            cb = getattr(self, "_on_exchange_fill_cb", None)
            if callable(cb):
                try:
                    await cb(line.side, res)
                except Exception:
                    _log.debug("post-fill ledger hook failed", exc_info=True)
            oid = str(res.get("orderId") or "")
            if oid:
                if self._record_filled_order_id(oid):
                    self._virtual_book.executions += 1
            else:
                self._virtual_book.executions += 1
            if line.side == Side.BUY:
                self._maybe_arm_paired_sell_after_buy(line.line_index)
            if line.side == Side.SELL and self._is_price_above_generator_upper(line.price):
                self._upper_sell_completed += 1
                asyncio.create_task(
                    self._maybe_auto_shift_after_upper_sell(float(mark)),
                    name="auto-shift-upper-sell",
                )
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
        finally:
            self._line_fill_mutex.discard(line.line_index)

    async def _io_dynamic_lift(self) -> None:
        """After band lift: disarm stale lower virtual lines and re-arm ladder at new band."""
        if not isinstance(self._exchange, BinanceSpotClient):
            return
        if self._virtual_book:
            lo = self._ram.generatorLower
            hi = self._ram.generatorUpper
            span = max(hi - lo, 1e-12)
            self._virtual_book.disarm_bucket("lower", lo=lo, hi=hi, span=span)
            mark = float(self._ram.last_price or hi)
            _, sell_rows = self._classify_levels_by_mark(
                mark,
                buys_only_strict_below=True,
            )
            self._upper_sell_armed_count = sum(
                1 for _idx, px in sell_rows if self._is_price_above_generator_upper(px)
            )
            self._upper_sell_completed = 0
            self._rearm_virtual_ladder(
                reason="grid_lift",
                mode="full",
                asymmetric_buys_only=True,
            )
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
        if self._asymmetric_shift_active and side == Side.BUY:
            style_probe = (
                self._virtual_book.order_style
                if self._virtual_book
                else grid_exec_settings()["order_style"]
            )
            if style_probe != ExecOrderStyle.LIMIT_IOC:
                _log.warning(
                    "blocked MARKET BUY during asymmetric auto-shift context=%s",
                    context,
                )
                self._audit(
                    SYSTEM_ERROR,
                    details={
                        "context": "asymmetric_shift_market_buy_blocked",
                        "spot_context": context,
                    },
                )
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
        target_px = float(price_s) if style == ExecOrderStyle.LIMIT_IOC else px
        try:
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
        except Exception as exc:
            from backend.api.grid_live_ledger import log_grid_ledger, parse_binance_api_error

            code, msg = parse_binance_api_error(exc)
            log_grid_ledger(
                self,
                action_type="API_FAILURE",
                trigger_reason=f"رفض أمر {side_u} ({context})",
                target_price=target_px,
                quantity=float(qty_s),
                api_error_code=code,
                api_error_message=msg,
                extra={"context": context, **(audit_extra or {})},
            )
            raise
        if not spot_order_filled(res):
            _log.debug(
                "spot order not filled context=%s status=%s side=%s",
                context,
                res.get("status"),
                side_u,
            )
            return res

        fill_px = target_px
        try:
            from backend.api.grid_live_ledger import fill_price_from_order_response

            fill_px = fill_price_from_order_response(res, target_px)
        except Exception:
            pass
        exec_qty = qty_s
        try:
            eq = float(res.get("executedQty") or 0)
            if eq > 0:
                exec_qty = str(eq)
        except (TypeError, ValueError):
            pass
        est_quote = float(exec_qty) * float(fill_px) if float(exec_qty) > 0 else 0.0
        audit_details = {
            **(audit_extra or {}),
            "context": context,
            "orderId": res.get("orderId"),
            "side": side_u,
            "quantity": str(exec_qty),
            "executedQty": str(exec_qty),
            "fill_price": float(fill_px),
            "line_price": float(target_px),
            "limit_price": float(price_s) if style == ExecOrderStyle.LIMIT_IOC else None,
            "mark_at_order": float(self._ram.last_price or 0.0),
            "order_style": style.value,
            "order_status": str(res.get("status") or "FILLED"),
            "estimated_gross_quote_usdt": round(est_quote, 8),
        }
        self._audit(audit_type, details=audit_details)
        try:
            from backend.api.grid_live_ledger import log_grid_order_fill

            log_grid_order_fill(
                self,
                side=side_u,
                order_id=res.get("orderId"),
                target_price=float(target_px),
                fill_price=float(fill_px),
                quantity=float(exec_qty),
                context=context,
                audit_extra=audit_details,
            )
        except Exception:
            _log.debug("grid ledger order fill log skipped", exc_info=True)
        return res

    async def _exit_line_market(self, line_idx: int, exit_snap: dict[str, Any] | None = None) -> None:
        if not isinstance(self._exchange, BinanceSpotClient):
            return
        st = self._ram.line_trail.get(line_idx)
        if st is None or not self._line_trail_allows_trailing(line_idx, st):
            return
        self._line_exit_mutex.add(line_idx)
        mark = float(self._ram.last_price or 0.0)
        side = Side.SELL if self._first_side == Side.BUY else Side.BUY
        qty = self._effective_order_qty()
        if self._virtual_book:
            vln = self._virtual_book.lines.get(line_idx)
            if vln is not None:
                qty = self._qty_for_virtual_line(vln, mark=mark or vln.price)
        if side == Side.SELL:
            qty = self._cap_sell_to_session_inventory(qty)
            if qty <= 0:
                self._audit(
                    SYSTEM_ERROR,
                    details={
                        "context": "trail_exit_no_session_inventory",
                        "line_index": line_idx,
                        "session_base_inventory": float(self._session_base_inventory),
                    },
                )
                return
        levels = self._rebuild_levels()
        if 0 <= line_idx < len(levels):
            ref_px = float(levels[line_idx])
            qty = self._cap_qty_to_line_budget(ref_px, qty, mark=mark or ref_px)
        snap = dict(exit_snap or {})
        try:
            exit_px = float(snap.get("stop_threshold_price") or 0.0)
            if exit_px <= 0:
                exit_px = float(snap.get("trail_peak") or mark or 0.0)
            res = await self._spot_execute(
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
            if res is not None and spot_order_filled(res):
                self._apply_session_inventory_delta(side, res)
                self._apply_exchange_fill_to_line_trail(line_idx, side)
                cb = getattr(self, "_on_exchange_fill_cb", None)
                if callable(cb):
                    try:
                        await cb(side, res)
                    except Exception:
                        _log.debug("trail exit ledger hook failed", exc_info=True)
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


def _compound_resize_pct_from_env(settings: dict[str, Any] | None = None) -> float:
    raw = ""
    if settings and settings.get("compound_resize_pct") is not None:
        raw = str(settings.get("compound_resize_pct"))
    if not raw.strip():
        raw = (os.getenv("ALKARRAR_COMPOUND_RESIZE_PCT") or "0.01").strip()
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = 0.01
    return max(0.0, min(v, 1.0))


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
