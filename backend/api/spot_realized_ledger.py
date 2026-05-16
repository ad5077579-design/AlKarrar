"""
FIFO spot ledger: realized USDT delta per acknowledged trade id.

Used by live grid_runner to derive ``realized_delta`` for shifting-grid profit injections.
Spot fills have no futures-style realizedPnl; we match SELL against prior BUY lots.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)


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
        elif str(row.get("S", "")).upper() in ("BUY", "SELL"):  # user stream
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
class SpotGridRealizedLedger:
    """
    Monotonic ``last_trade_id``; ``buy_lots`` queue holds (qty_base, avg_quote_per_base).
    """

    last_trade_id: int = 0
    buy_lots: deque[tuple[float, float]] = field(default_factory=deque)

    def reset_session(self, *, anchor_last_trade_id: int | None = None) -> None:
        self.buy_lots.clear()
        self.last_trade_id = int(anchor_last_trade_id or 0)

    def seed_history(self, rows: list[dict[str, Any]], *, mute_realized: bool = True) -> None:
        """
        Replay chronological fills to rebuild inventory (muted realized so hub injection
        doesn't spike on bootstrap).
        """
        batch = [normalize_trade_row_for_ledger(r) for r in rows]
        batch = [b for b in batch if b is not None]
        batch.sort(key=lambda x: int(x["id"]))
        for b in batch:
            self.ingest_normalized(b, mute_realized=mute_realized)

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
            cost = quote + comm_u
            unit = cost / qty if qty > 0 else 0.0
            self.buy_lots.append((qty, unit))
        else:
            proceeds = max(quote - comm_u, 0.0)
            rem = qty
            cost_basis = 0.0
            eps = max(qty * 1e-9, 1e-12)
            while rem > eps and self.buy_lots:
                q0, u0 = self.buy_lots[0]
                take = min(rem, q0)
                cost_basis += take * u0
                rem -= take
                nq = q0 - take
                if nq <= eps:
                    self.buy_lots.popleft()
                else:
                    self.buy_lots[0] = (nq, u0)
            realized = proceeds - cost_basis

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


def journal_row_to_ledger_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """executionReport / WS ``trade`` payload already journal-shaped."""
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

