"""
FIFO spot ledger: realized USDT per trade id (session-isolated inventory).

Used by live grid_runner for ``realized_delta`` (compounding). Spot fills have no
futures-style realizedPnl; SELL is matched only against BUY lots recorded in this
session — unmatched base does not inflate realized PnL.

Realized PnL (matched slice): (sell_quote - sell_commission) - sum(buy_cost_basis).
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

MIN_USDT_PER_LINE_DEFAULT = 11.0
MIN_LINE_SPACING_PCT_DEFAULT = 0.0015  # 0.15% — covers ~0.1% fee × 2


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def parse_base_quote(symbol: str) -> tuple[str, str]:
    s = str(symbol).upper().replace("/", "")
    if s.endswith("USDT"):
        return s[:-4], "USDT"
    if s.endswith("USDC"):
        return s[:-4], "USDC"
    if s.endswith("BUSD"):
        return s[:-4], "BUSD"
    return s, "USDT"


def commission_to_usdt(
    *,
    commission: float,
    commission_asset: str,
    price: float,
    symbol: str,
) -> float:
    if commission <= 0:
        return 0.0
    base, quote = parse_base_quote(symbol)
    a = str(commission_asset or "").upper()
    if a == quote or a in ("USDT", "USDC", "BUSD"):
        return commission
    if a == base:
        return commission * price
    return 0.0


def coerce_trade_id(row: dict[str, Any]) -> int:
    raw = row.get("id")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    et = row.get("exchangeTradeId")
    if et is not None and str(et).isdigit():
        return int(str(et))
    return 0


def normalize_trade_row_for_ledger(row: dict[str, Any]) -> dict[str, Any] | None:
    """Accept myTrades or trade_journal-shaped dicts."""
    if not isinstance(row, dict):
        return None
    tid = coerce_trade_id(row)
    if tid <= 0:
        return None
    sym = str(row.get("symbol", "")).upper().replace("/", "")
    if not sym:
        return None
    price = _f(row.get("price"))
    qty = _f(row.get("quantity") if row.get("quantity") is not None else row.get("qty"))
    if price <= 0 or qty <= 0:
        return None
    side = str(row.get("side", "")).upper()
    if side not in ("BUY", "SELL"):
        if "isBuyer" in row:
            side = "BUY" if bool(row.get("isBuyer")) else "SELL"
        elif str(row.get("S", "")).upper() in ("BUY", "SELL"):
            side = str(row.get("S", "")).upper()
    if side not in ("BUY", "SELL"):
        return None
    quote = _f(row.get("quoteQty"))
    if quote <= 0:
        quote = price * qty
    return {
        "id": tid,
        "symbol": sym,
        "price": price,
        "qty": qty,
        "quoteQty": quote,
        "side": side,
        "commission": _f(row.get("commission")),
        "commissionAsset": str(row.get("commissionAsset") or row.get("N") or ""),
    }


@dataclass
class BuyLot:
    """One FIFO buy lot: full cost in quote terms per base unit."""

    qty_base: float
    unit_cost_usdt: float


@dataclass
class SpotGridRealizedLedger:
    """
    Monotonic ``last_trade_id``; ``buy_lots`` holds session BUY inventory only.

    Pre-session wallet inventory is never imported — SELL without a matching lot
    yields zero realized PnL for the unmatched quantity.
    """

    last_trade_id: int = 0
    buy_lots: deque[BuyLot] = field(default_factory=deque)
    session_isolated: bool = True

    def reset_session(self, *, anchor_last_trade_id: int | None = None) -> None:
        self.buy_lots.clear()
        self.last_trade_id = int(anchor_last_trade_id or 0)

    def anchor_trade_cursor(self, rows: list[dict[str, Any]]) -> None:
        """
        Advance ``last_trade_id`` from historical rows without building buy inventory.
        Prevents replaying wallet history into compounding on the next poll.
        """
        mx = self.last_trade_id
        for row in rows:
            b = normalize_trade_row_for_ledger(row)
            if b is None:
                continue
            mx = max(mx, int(b["id"]))
        self.last_trade_id = mx

    def seed_history(self, rows: list[dict[str, Any]], *, mute_realized: bool = True) -> None:
        """
        Legacy name: when ``session_isolated`` (default), only anchors the trade cursor.
        """
        if self.session_isolated:
            self.anchor_trade_cursor(rows)
            return
        batch = [normalize_trade_row_for_ledger(r) for r in rows]
        batch = [b for b in batch if b is not None]
        batch.sort(key=lambda x: int(x["id"]))
        for b in batch:
            self.ingest_normalized(b, mute_realized=mute_realized)

    @staticmethod
    def realized_pnl_usdt(
        *,
        sell_qty: float,
        sell_price: float,
        buy_unit_cost: float,
        sell_commission_usdt: float = 0.0,
        buy_commission_usdt: float = 0.0,
    ) -> float:
        """Explicit: (sell - buy) * qty minus commissions on the matched slice."""
        q = max(float(sell_qty), 0.0)
        if q <= 0:
            return 0.0
        proceeds = q * float(sell_price) - float(sell_commission_usdt)
        cost = q * float(buy_unit_cost) + float(buy_commission_usdt)
        return float(proceeds - cost)

    def _append_buy_lot(self, qty: float, *, quote: float, commission_usdt: float) -> None:
        if qty <= 0:
            return
        cost = quote + commission_usdt
        unit = cost / qty
        self.buy_lots.append(BuyLot(qty_base=qty, unit_cost_usdt=unit))

    def ingest_normalized(self, b: dict[str, Any], *, mute_realized: bool = False) -> float:
        tid = int(b["id"])
        if tid <= self.last_trade_id:
            return 0.0
        symbol = str(b["symbol"])
        price = float(b["price"])
        qty = float(b["qty"])
        quote = float(b["quoteQty"])
        side = str(b["side"]).upper()
        comm_u = commission_to_usdt(
            commission=float(b["commission"]),
            commission_asset=str(b.get("commissionAsset") or ""),
            price=price,
            symbol=symbol,
        )

        realized = 0.0
        if side == "BUY":
            self._append_buy_lot(qty, quote=quote, commission_usdt=comm_u)
        else:
            proceeds_total = max(quote - comm_u, 0.0)
            rem = qty
            cost_basis = 0.0
            matched_qty = 0.0
            eps = max(qty * 1e-9, 1e-12)
            while rem > eps and self.buy_lots:
                lot = self.buy_lots[0]
                take = min(rem, lot.qty_base)
                cost_basis += take * lot.unit_cost_usdt
                matched_qty += take
                rem -= take
                nq = lot.qty_base - take
                if nq <= eps:
                    self.buy_lots.popleft()
                else:
                    self.buy_lots[0] = BuyLot(qty_base=nq, unit_cost_usdt=lot.unit_cost_usdt)
            if matched_qty > eps and qty > 0:
                proceeds_matched = proceeds_total * (matched_qty / qty)
                realized = proceeds_matched - cost_basis
            if rem > eps:
                _log.warning(
                    "spot ledger: SELL %.8f base unmatched (no session BUY lot) — "
                    "excluded from realized PnL",
                    rem,
                )

        self.last_trade_id = tid
        out = realized if side == "SELL" else 0.0
        return 0.0 if mute_realized else float(out)

    def ingest_many(self, rows: list[dict[str, Any]]) -> float:
        """Returns sum realized USDT across new trades (strictly increasing id)."""
        norm = [normalize_trade_row_for_ledger(r) for r in rows]
        norm = [b for b in norm if b is not None]
        norm.sort(key=lambda x: int(x["id"]))
        total = 0.0
        for b in norm:
            total += self.ingest_normalized(b, mute_realized=False)
        return float(total)

    def ingest_order_fills(self, res: dict[str, Any], *, symbol: str) -> float:
        """
        Record fills from a Binance order response (``fills`` array or top-level qty).
        Returns realized USDT delta (usually 0 on BUY).
        """
        sym = symbol.upper().replace("/", "")
        fills = res.get("fills")
        if isinstance(fills, list) and fills:
            total = 0.0
            for f in fills:
                if not isinstance(f, dict):
                    continue
                tid_raw = f.get("tradeId") or f.get("id")
                try:
                    tid = int(tid_raw)
                except (TypeError, ValueError):
                    continue
                price = _f(f.get("price"))
                qty = _f(f.get("qty"))
                if price <= 0 or qty <= 0:
                    continue
                comm = _f(f.get("commission"))
                comm_asset = str(f.get("commissionAsset") or "")
                is_buy = bool(res.get("side", "").upper() == "BUY" or f.get("isBuyer"))
                row = {
                    "id": tid,
                    "symbol": sym,
                    "price": price,
                    "qty": qty,
                    "quoteQty": price * qty,
                    "side": "BUY" if is_buy else "SELL",
                    "commission": comm,
                    "commissionAsset": comm_asset,
                }
                total += self.ingest_normalized(row, mute_realized=False)
            return float(total)

        # Without per-fill trade ids, defer to myTrades poll (orderId != trade id).
        return 0.0

    def open_inventory_cost_usdt(self) -> float:
        return float(sum(lot.qty_base * lot.unit_cost_usdt for lot in self.buy_lots))

    def open_inventory_base_qty(self) -> float:
        return float(sum(lot.qty_base for lot in self.buy_lots))

    def cap_sell_base_qty(self, requested_qty: float) -> float:
        """Ring-fence: never sell more base than session BUY lots hold."""
        req = max(float(requested_qty), 0.0)
        avail = self.open_inventory_base_qty()
        if req <= avail:
            return req
        if avail <= 0:
            return 0.0
        return avail

    def unrealized_pnl_usdt(self, mark_price: float) -> float:
        mk = float(mark_price)
        if mk <= 0:
            return 0.0
        cost = self.open_inventory_cost_usdt()
        base = self.open_inventory_base_qty()
        if base <= 0:
            return 0.0
        return float(base * mk - cost)


def journal_row_to_ledger_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    merged = dict(row)
    merged["qty"] = merged.get("quantity", merged.get("qty"))
    if merged.get("id") is None and merged.get("exchangeTradeId"):
        try:
            merged["id"] = int(str(merged["exchangeTradeId"]))
        except (TypeError, ValueError):
            return None
    return normalize_trade_row_for_ledger(merged)


def validate_grid_economics(
    *,
    generator_upper: float,
    generator_lower: float,
    generator_count: int,
    allocated_capital: float,
    min_usdt_per_line: float = MIN_USDT_PER_LINE_DEFAULT,
    min_line_spacing_pct: float = MIN_LINE_SPACING_PCT_DEFAULT,
) -> None:
    """
    Pre-flight: block grids that cannot clear Binance MIN_NOTIONAL / fee round-trip.
    """
    upper = float(generator_upper)
    lower = float(generator_lower)
    count = max(int(generator_count), 2)
    alloc = float(allocated_capital)
    if alloc <= 0:
        raise ValueError("allocatedCapital must be > 0")
    if not (lower < upper):
        raise ValueError("require generatorLower < generatorUpper")
    per_line = alloc / count
    if per_line < min_usdt_per_line:
        raise ValueError(
            f"حجم الخط ({per_line:.2f} USDT) أقل من الحد الأدنى {min_usdt_per_line:.0f} USDT "
            f"(allocatedCapital / generatorCount)"
        )
    mid = (upper + lower) / 2.0
    if mid <= 0:
        raise ValueError("نطاق الشبكة غير صالح")
    span_pct = (upper - lower) / mid
    spacing_pct = span_pct / max(count - 1, 1)
    if spacing_pct < min_line_spacing_pct:
        raise ValueError(
            f"المسافة بين الخطوط ({spacing_pct * 100:.3f}%) أقل من الحد "
            f"{min_line_spacing_pct * 100:.2f}% — ضيّق النطاق أو قلّل generatorCount"
        )


def validate_trailing_offset(
    *,
    trailing_offset: float,
    mark_price: float,
    min_pct_of_mark: float = 0.001,
) -> None:
    """Trailing offset must be meaningful vs price (avoids all lines hitting TP at once)."""
    off = float(trailing_offset)
    mk = float(mark_price)
    if mk <= 0 or off <= 0:
        return
    if off / mk < min_pct_of_mark:
        raise ValueError(
            f"trailingOffset ({off:.8g}) صغير جداً مقارنة بالسعر ({mk:.6g}) — "
            f"استخدم على الأقل {mk * min_pct_of_mark:.6g} (~{min_pct_of_mark * 100:.2f}% من Mark)"
        )


def band_mid_deviation_pct(
    *,
    generator_upper: float,
    generator_lower: float,
    mark_price: float,
) -> float:
    upper = float(generator_upper)
    lower = float(generator_lower)
    mark = float(mark_price)
    if mark <= 0 or not (lower < upper):
        return 0.0
    mid = (upper + lower) / 2.0
    return abs(mid - mark) / mark


def band_from_mark_span(
    mark_price: float,
    *,
    span_pct: float = 3.5,
    tick_size: float = 0.0,
) -> tuple[float, float]:
    """Symmetric band around live mark (percent span of midpoint)."""
    mark = float(mark_price)
    if mark <= 0:
        raise ValueError("mark_price must be > 0")
    half = mark * (float(span_pct) / 200.0)
    lower = mark - half
    upper = mark + half
    if tick_size > 0:
        from backend.core.exchange_filters import quantize_price

        lower = quantize_price(lower, tick_size)
        upper = quantize_price(upper, tick_size)
    if lower <= 0:
        lower = mark * 0.985
        if tick_size > 0:
            from backend.core.exchange_filters import quantize_price

            lower = quantize_price(lower, tick_size)
    return float(lower), float(upper)


def validate_band_matches_symbol_mark(
    *,
    generator_upper: float,
    generator_lower: float,
    mark_price: float,
    symbol: str = "",
    max_mid_deviation_pct: float = 0.35,
) -> None:
    """
    Reject grids whose band midpoint is far from the symbol's live mark (cross-symbol contamination).
    """
    upper = float(generator_upper)
    lower = float(generator_lower)
    mark = float(mark_price)
    if mark <= 0 or not (lower < upper):
        return
    mid = (upper + lower) / 2.0
    dev = abs(mid - mark) / mark
    if dev > max_mid_deviation_pct:
        sym = str(symbol or "").upper().replace("/", "") or "?"
        raise ValueError(
            f"نطاق {sym} (وسط {mid:.6g}) لا يطابق السعر الحي {mark:.6g} "
            f"(انحراف {dev * 100:.1f}%) — راجع generatorUpper/Lower لهذا الزوج فقط"
        )
