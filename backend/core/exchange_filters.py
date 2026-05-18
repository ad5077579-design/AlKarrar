"""Binance Spot symbol filters: quantize price/qty for valid orders."""

from __future__ import annotations

import math
from typing import Any


def _quantize(value: float, step: float) -> float:
    if step <= 0:
        return value
    q = math.floor(value / step + 1e-12) * step
    decimals = 8
    if step < 1 and step > 0:
        decimals = min(8, max(0, int(round(-math.log10(step))) + 2))
    return round(q, decimals)


def parse_symbol_filters(row: dict[str, Any]) -> dict[str, float]:
    out = {
        "tick_size": 0.0,
        "step_size": 0.0,
        "min_qty": 0.0,
        "min_notional": 5.0,
    }
    for f in row.get("filters") or []:
        if not isinstance(f, dict):
            continue
        ft = f.get("filterType")
        if ft == "PRICE_FILTER":
            out["tick_size"] = float(f.get("tickSize", 0) or 0)
        elif ft == "LOT_SIZE":
            out["step_size"] = float(f.get("stepSize", 0) or 0)
            out["min_qty"] = float(f.get("minQty", 0) or 0)
        elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
            out["min_notional"] = float(f.get("notional", f.get("minNotional", 5)) or 5)
    return out


async def fetch_symbol_filters(client: Any, symbol: str) -> dict[str, float]:
    info = await client.get_exchange_info()
    sym = symbol.upper().replace("/", "")
    for row in info.get("symbols") or []:
        if isinstance(row, dict) and str(row.get("symbol", "")).upper() == sym:
            return parse_symbol_filters(row)
    raise ValueError(f"symbol not listed: {sym}")


def quantize_price(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return price
    return _quantize(price, tick_size)


def quantize_qty(qty: float, *, step_size: float, min_qty: float, min_notional: float, price: float) -> float:
    need = max(min_qty, min_notional / max(price, 1e-12))
    q = max(qty, need)
    if step_size > 0:
        q = math.ceil(q / step_size - 1e-12) * step_size
        if q < min_qty:
            q = min_qty
    return _quantize(q, step_size) if step_size > 0 else q


def _decimals_for_step(step: float) -> int:
    if step <= 0:
        return 8
    if step >= 1:
        return 0
    return min(8, max(0, int(round(-math.log10(step)))))


def format_decimal(value: float, step: float) -> str:
    """String form acceptable to Binance REST (no excess precision)."""
    d = _decimals_for_step(step)
    if d <= 0:
        return str(int(round(value)))
    return f"{value:.{d}f}".rstrip("0").rstrip(".") or "0"


def min_trade_qty(price: float, filters: dict[str, float]) -> float:
    """Minimum tradable base quantity at ``price`` (lot + notional rules)."""
    return quantize_qty(
        filters.get("min_qty", 0) or 0,
        step_size=filters.get("step_size", 0),
        min_qty=filters.get("min_qty", 0),
        min_notional=filters.get("min_notional", 5.0),
        price=price,
    )


def format_base_qty_from_usdt_slice(
    *,
    usdt_per_line: float,
    mark: float,
    filters: dict[str, float],
) -> tuple[float, str]:
    """
    Convert a USDT allocation per grid line into a Binance-valid base quantity string.

    Applies PRICE_FILTER (via mark) and LOT_SIZE + MIN_NOTIONAL so downstream orders avoid -1013.
    """
    if usdt_per_line <= 0 or mark <= 0:
        return 0.0, "0"
    raw_qty = usdt_per_line / mark
    _, qty_s = normalize_order(mark, raw_qty, filters)
    try:
        qty_f = float(qty_s)
    except (TypeError, ValueError):
        qty_f = 0.0
    return qty_f, qty_s


def normalize_order(price: float, qty: float, filters: dict[str, float]) -> tuple[str, str]:
    tick = filters.get("tick_size", 0)
    step = filters.get("step_size", 0)
    min_qty = filters.get("min_qty", 0)
    min_notional = filters.get("min_notional", 5.0)
    p = quantize_price(price, tick)
    q = quantize_qty(
        qty,
        step_size=step,
        min_qty=min_qty,
        min_notional=min_notional,
        price=p,
    )
    return format_decimal(p, tick), format_decimal(q, step)
