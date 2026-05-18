"""
In-memory per-symbol grid ledger (no SQLite). WebSocket + REST snapshot for UI.

Manual grid stop → flush. Emergency / trailing stop → freeze until restart or user clear.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

MAX_ENTRIES_PER_SYMBOL = 400
_IOC_MISS_LEDGER_SKIP_CTX = frozenset(
    {
        "virtual_grid_no_exchange_fill",
        "virtual_grid_fill_no_exchange_fill",
        "virtual_grid_slippage",
    }
)
_FAILURE_DEDUPE_MS = 60_000

_API_CODE_RE = re.compile(r"(?:code|error)[:\s]*(-?\d{3,5})", re.I)
_BINANCE_CODE_RE = re.compile(r"(-?\d{4,5})")


def parse_binance_api_error(exc: BaseException | str | None) -> tuple[str | None, str]:
    if exc is None:
        return None, ""
    msg = str(exc).strip()
    if not msg:
        return None, ""
    m = _API_CODE_RE.search(msg) or _BINANCE_CODE_RE.search(msg)
    code = m.group(1) if m else None
    return code, msg[:500]


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class GridLedgerEntry:
    id: str
    timestamp_ms: int
    action_type: str
    trigger_reason: str
    symbol: str
    target_price: float | None = None
    fill_price: float | None = None
    slippage_pct: float | None = None
    quantity: float | None = None
    net_profit_usdt: float | None = None
    commission_usdt: float | None = None
    generator_upper: float = 0.0
    generator_lower: float = 0.0
    generator_count: int = 0
    order_size: float = 0.0
    mark_price: float = 0.0
    api_error_code: str | None = None
    api_error_message: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestampMs": self.timestamp_ms,
            "actionType": self.action_type,
            "triggerReason": self.trigger_reason,
            "symbol": self.symbol,
            "targetPrice": self.target_price,
            "fillPrice": self.fill_price,
            "slippagePct": self.slippage_pct,
            "quantity": self.quantity,
            "netProfitUsdt": self.net_profit_usdt,
            "commissionUsdt": self.commission_usdt,
            "generatorUpper": self.generator_upper,
            "generatorLower": self.generator_lower,
            "generatorCount": self.generator_count,
            "orderSize": self.order_size,
            "markPrice": self.mark_price,
            "apiErrorCode": self.api_error_code,
            "apiErrorMessage": self.api_error_message,
            "extra": dict(self.extra),
        }


@dataclass
class _SymbolLedger:
    bot_id: str = "default"
    frozen: bool = False
    freeze_reason: str = ""
    entries: deque[GridLedgerEntry] = field(default_factory=lambda: deque(maxlen=MAX_ENTRIES_PER_SYMBOL))


class GridLiveLedgerStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_symbol: dict[str, _SymbolLedger] = {}
        self._dedupe_oid_ms: dict[str, dict[str, int]] = {}
        self._dedupe_fail_ms: dict[str, dict[str, int]] = {}

    def _norm(self, symbol: str) -> str:
        return symbol.strip().upper().replace("/", "")

    def _get(self, symbol: str) -> _SymbolLedger:
        sym = self._norm(symbol)
        if sym not in self._by_symbol:
            self._by_symbol[sym] = _SymbolLedger()
        return self._by_symbol[sym]

    def begin_session(self, symbol: str, *, bot_id: str = "default") -> None:
        sym = self._norm(symbol)
        with self._lock:
            self._by_symbol[sym] = _SymbolLedger(bot_id=bot_id, frozen=False, freeze_reason="")
            self._dedupe_oid_ms.pop(sym, None)
            self._dedupe_fail_ms.pop(sym, None)
        self._schedule_ws(sym, cleared=True)

    def flush_manual_stop(self, symbol: str) -> None:
        sym = self._norm(symbol)
        with self._lock:
            if sym in self._by_symbol:
                del self._by_symbol[sym]
        self._schedule_ws(sym, cleared=True)

    def freeze(self, symbol: str, *, reason: str = "emergency_stop") -> None:
        sym = self._norm(symbol)
        with self._lock:
            led = self._get(sym)
            led.frozen = True
            led.freeze_reason = reason[:200]
        self._schedule_ws(sym, frozen=True)

    def freeze_all_active(self, symbols: list[str], *, reason: str = "emergency_stop") -> None:
        for s in symbols:
            self.freeze(s, reason=reason)

    def clear_user(self, symbol: str) -> bool:
        sym = self._norm(symbol)
        with self._lock:
            led = self._by_symbol.get(sym)
            if led is None:
                return True
            if not led.frozen and led.entries:
                return False
            del self._by_symbol[sym]
        self._schedule_ws(sym, cleared=True)
        return True

    def snapshot(self, symbol: str) -> dict[str, Any]:
        sym = self._norm(symbol)
        with self._lock:
            led = self._by_symbol.get(sym)
            if led is None:
                return {
                    "symbol": sym,
                    "frozen": False,
                    "freezeReason": "",
                    "count": 0,
                    "entries": [],
                }
            return {
                "symbol": sym,
                "botId": led.bot_id,
                "frozen": led.frozen,
                "freezeReason": led.freeze_reason,
                "count": len(led.entries),
                "entries": [e.to_dict() for e in list(led.entries)],
            }

    def append(
        self,
        symbol: str,
        *,
        bot_id: str = "default",
        action_type: str,
        trigger_reason: str,
        target_price: float | None = None,
        fill_price: float | None = None,
        quantity: float | None = None,
        net_profit_usdt: float | None = None,
        commission_usdt: float | None = None,
        generator_upper: float = 0.0,
        generator_lower: float = 0.0,
        generator_count: int = 0,
        order_size: float = 0.0,
        mark_price: float = 0.0,
        api_error_code: str | None = None,
        api_error_message: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> GridLedgerEntry | None:
        sym = self._norm(symbol)
        if not sym:
            return None
        slip: float | None = None
        if target_price and fill_price and target_price > 0 and fill_price > 0:
            slip = round(((fill_price - target_price) / target_price) * 100.0, 6)

        ts = _now_ms()
        entry = GridLedgerEntry(
            id=uuid.uuid4().hex[:12],
            timestamp_ms=ts,
            action_type=action_type[:64],
            trigger_reason=trigger_reason[:1024],
            symbol=sym,
            target_price=target_price,
            fill_price=fill_price,
            slippage_pct=slip,
            quantity=quantity,
            net_profit_usdt=net_profit_usdt,
            commission_usdt=commission_usdt,
            generator_upper=float(generator_upper),
            generator_lower=float(generator_lower),
            generator_count=int(generator_count),
            order_size=float(order_size),
            mark_price=float(mark_price),
            api_error_code=api_error_code,
            api_error_message=api_error_message,
            extra=dict(extra or {}),
        )
        with self._lock:
            led = self._get(sym)
            led.bot_id = bot_id or led.bot_id
            ctx = str((extra or {}).get("context") or trigger_reason or "")
            line_idx = (extra or {}).get("line_index")
            if action_type == "API_FAILURE":
                fail_key = f"{ctx}:{line_idx}"
                by_fail = self._dedupe_fail_ms.setdefault(sym, {})
                prev_fail = by_fail.get(fail_key)
                if prev_fail is not None and abs(int(ts) - int(prev_fail)) <= _FAILURE_DEDUPE_MS:
                    for existing in reversed(led.entries):
                        ex = existing.extra or {}
                        if (
                            existing.action_type == "API_FAILURE"
                            and str(ex.get("context") or existing.trigger_reason) == ctx
                            and ex.get("line_index") == line_idx
                        ):
                            return existing
                by_fail[fail_key] = int(ts)
                if len(by_fail) > 400:
                    fail_cutoff = int(ts) - 120_000
                    for k, v in list(by_fail.items()):
                        if v < fail_cutoff:
                            del by_fail[k]
            oid_raw = (extra or {}).get("orderId")
            if oid_raw is not None:
                oid_s = str(oid_raw)
                dedupe_key = f"{action_type[:64]}:{oid_s}"
                by_oid = self._dedupe_oid_ms.setdefault(sym, {})
                prev_ts = by_oid.get(dedupe_key)
                if prev_ts is not None and abs(int(ts) - int(prev_ts)) <= 30_000:
                    for existing in reversed(led.entries):
                        if (
                            str(existing.extra.get("orderId")) == oid_s
                            and existing.action_type == action_type[:64]
                        ):
                            return existing
                by_oid[dedupe_key] = int(ts)
                if len(by_oid) > 400:
                    cutoff = int(ts) - 120_000
                    for k, v in list(by_oid.items()):
                        if v < cutoff:
                            del by_oid[k]
            led.entries.append(entry)
            frozen = led.frozen
        self._schedule_ws(sym, entry=entry, frozen=frozen)
        return entry

    def _schedule_ws(
        self,
        symbol: str,
        *,
        entry: GridLedgerEntry | None = None,
        cleared: bool = False,
        frozen: bool | None = None,
    ) -> None:
        async def _send() -> None:
            from backend.api.bot_hub import hub

            payload: dict[str, Any] = {"type": "grid_ledger", "symbol": symbol}
            if cleared:
                payload["cleared"] = True
                payload["entries"] = []
                payload["frozen"] = False
                payload["freezeReason"] = ""
            else:
                snap = self.snapshot(symbol)
                payload["frozen"] = snap["frozen"]
                payload["freezeReason"] = snap.get("freezeReason", "")
                if entry is not None:
                    payload["entry"] = entry.to_dict()
                payload["count"] = snap["count"]
            if frozen is not None:
                payload["frozen"] = frozen
            await hub.broadcast_room(symbol, payload)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_send(), name=f"grid-ledger-ws-{symbol}")
        except RuntimeError:
            pass


grid_live_ledger = GridLiveLedgerStore()


def slippage_pct(target: float | None, fill: float | None) -> float | None:
    if not (target and fill and target > 0):
        return None
    return round(((fill - target) / target) * 100.0, 6)


def fill_price_from_order_response(res: dict[str, Any] | None, fallback: float) -> float:
    """Prefer actual fill VWAP from ``fills`` / quote qty — not limit ``price`` (IOC)."""
    if not res:
        return fallback
    fills = res.get("fills")
    if isinstance(fills, list) and fills:
        total_qty = 0.0
        total_quote = 0.0
        for f in fills:
            if not isinstance(f, dict):
                continue
            try:
                q = float(f.get("qty") or 0)
                p = float(f.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if q > 0 and p > 0:
                total_qty += q
                total_quote += q * p
        if total_qty > 0:
            return total_quote / total_qty
    try:
        exec_qty = float(res.get("executedQty") or res.get("origQty") or 0)
        quote = float(
            res.get("cummulativeQuoteQty")
            or res.get("cumulativeQuoteQty")
            or 0
        )
        if exec_qty > 0 and quote > 0:
            return quote / exec_qty
    except (TypeError, ValueError):
        pass
    for key in ("avgPrice", "price"):
        try:
            v = float(res.get(key) or 0)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return fallback


def grid_state_from_strategy(strategy: Any) -> dict[str, float | int]:
    ram = getattr(strategy, "_ram", None)
    if ram is None:
        return {
            "generator_upper": 0.0,
            "generator_lower": 0.0,
            "generator_count": 0,
            "order_size": 0.0,
            "mark_price": 0.0,
        }
    return {
        "generator_upper": float(getattr(ram, "generatorUpper", 0) or 0),
        "generator_lower": float(getattr(ram, "generatorLower", 0) or 0),
        "generator_count": int(getattr(ram, "generatorCount", 0) or 0),
        "order_size": float(getattr(ram, "order_quantity_effective", 0) or 0),
        "mark_price": float(getattr(ram, "last_price", 0) or 0),
    }


def log_grid_order_fill(
    strategy: Any,
    *,
    side: str,
    order_id: str | int | None,
    target_price: float,
    fill_price: float,
    quantity: float,
    context: str,
    audit_extra: dict[str, Any] | None = None,
) -> None:
    """Single ledger row per exchange order (avoids duplicate audit + fill paths)."""
    side_u = str(side).upper()
    action = "ORDER_BUY" if side_u == "BUY" else "ORDER_SELL"
    extra = dict(audit_extra or {})
    if order_id is not None:
        extra["orderId"] = order_id
    extra.setdefault("side", side_u)
    extra.setdefault("context", context)
    _reasons = {
        "virtual_grid_fill": "تنفيذ خط شبكة عند تقاطع Mark",
        "take_profit_trailing_exit": "خروج Trailing / جني ربح على الخط",
        "bootstrap_market_buy": "شراء افتتاحي لتجهيز الشبكة",
    }
    reason = _reasons.get(context, str(context or "تنفيذ أمر على البورصة"))
    log_grid_ledger(
        strategy,
        action_type=action,
        trigger_reason=reason,
        target_price=float(target_price),
        fill_price=float(fill_price),
        quantity=float(quantity),
        extra=extra,
    )


def log_grid_ledger(
    strategy: Any,
    *,
    action_type: str,
    trigger_reason: str,
    target_price: float | None = None,
    fill_price: float | None = None,
    quantity: float | None = None,
    net_profit_usdt: float | None = None,
    commission_usdt: float | None = None,
    api_error_code: str | None = None,
    api_error_message: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    sym = str(getattr(strategy, "_symbol", "") or "").upper().replace("/", "")
    if not sym:
        return
    gs = grid_state_from_strategy(strategy)
    grid_live_ledger.append(
        sym,
        bot_id=str(getattr(strategy, "_bot_id", None) or "default"),
        action_type=action_type,
        trigger_reason=trigger_reason,
        target_price=target_price,
        fill_price=fill_price,
        quantity=quantity,
        net_profit_usdt=net_profit_usdt,
        commission_usdt=commission_usdt,
        api_error_code=api_error_code,
        api_error_message=api_error_message,
        generator_upper=float(gs["generator_upper"]),
        generator_lower=float(gs["generator_lower"]),
        generator_count=int(gs["generator_count"]),
        order_size=float(gs["order_size"]),
        mark_price=float(gs["mark_price"]),
        extra=extra,
    )


def log_from_audit_event(
    strategy: Any,
    event_type: str,
    *,
    realized_usdt: float = 0.0,
    details: dict[str, Any] | None = None,
) -> None:
    """Map legacy audit events to grid ledger rows (in-memory only)."""
    d = dict(details or {})
    side = str(d.get("side", "")).upper()
    action = "SYSTEM"
    reason = str(d.get("context") or d.get("shift_reason") or event_type)

    if event_type == "GRID_SHIFT":
        action = "AUTO_SHIFT_UP"
        reason = str(
            d.get("shift_reason")
            or "رفع النطاق — السعر تجاوز generatorUpper أو إغلاق آخر خط بيع علوي"
        )
    elif event_type in ("VIRTUAL_GRID_FILL", "TAKE_PROFIT_MARKET"):
        return
    elif event_type == "PROFIT_INJECT_COMPOUND":
        action = "RESIZE_LOT"
        reason = str(
            d.get("reason")
            or "إعادة تسعير حجم الخط — الربح التراكمي تجاوز عتبة compound_resize"
        )
    elif event_type == "TRAILING_STARTED":
        if not d.get("exchange_fill_confirmed"):
            return
        line_idx = d.get("line_index")
        has_buy = getattr(strategy, "_line_has_confirmed_buy", None)
        if line_idx is not None and callable(has_buy) and not has_buy(int(line_idx)):
            return
        action = "TRAILING_ARM"
        reason = "تفعيل مرحلة Trailing بعد لمس مستوى الربح"
    elif event_type == "VIRTUAL_GRID_ARMED":
        return
    elif event_type == "SYSTEM_ERROR":
        ctx = str(d.get("context") or "")
        if ctx in _IOC_MISS_LEDGER_SKIP_CTX or ctx.endswith("_no_exchange_fill"):
            return
        action = "API_FAILURE"
        err = str(d.get("error") or "")
        code, msg = parse_binance_api_error(err)
        log_grid_ledger(
            strategy,
            action_type=action,
            trigger_reason=str(d.get("context") or "خطأ تنفيذ"),
            api_error_code=code,
            api_error_message=msg or err[:500],
            extra=d,
        )
        return
    elif event_type == "VIRTUAL_REARM":
        action = "GRID_REARM"
        reason = f"إعادة تسليح الشبكة ({d.get('reason', '')})"
    elif event_type == "VIRTUAL_PAIR_SELL_ARMED":
        action = "PAIR_SELL_ARMED"
        reason = "تفعيل خط بيع بعد شراء (شبكة غير متماثلة بعد الرفع)"

    target = d.get("line_price") or d.get("limit_price")
    try:
        target_f = float(target) if target is not None else None
    except (TypeError, ValueError):
        target_f = None
    fill_raw = d.get("fill_price")
    if fill_raw is None:
        fill_raw = d.get("mark_at_order") or d.get("mark")
    try:
        fill_f = float(fill_raw) if fill_raw is not None else target_f
    except (TypeError, ValueError):
        fill_f = target_f
    qty_raw = d.get("quantity") or d.get("executedQty")
    try:
        qty_f = float(qty_raw) if qty_raw is not None else None
    except (TypeError, ValueError):
        qty_f = None

    log_grid_ledger(
        strategy,
        action_type=action,
        trigger_reason=reason,
        target_price=target_f,
        fill_price=fill_f,
        quantity=qty_f,
        net_profit_usdt=float(realized_usdt) if realized_usdt else None,
        extra=d,
    )
