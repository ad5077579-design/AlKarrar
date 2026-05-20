"""
Strict Spot USDT grid suitability scan — ranks symbols like a disciplined grid trader.

Only symbols that pass AlKarrar economics (line spacing, per-line USDT, min notional)
and default calibrated bands are returned. No random picks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.api.spot_realized_ledger import (
    MIN_LINE_SPACING_PCT_DEFAULT,
    MIN_USDT_PER_LINE_DEFAULT,
    band_from_mark_span,
    compute_grid_line_limits,
    validate_grid_economics,
)
from backend.core.exchange_filters import parse_symbol_filters
from backend.core.spot_market_filters import is_grid_tradable_base, normalize_symbol

# --- Criteria (documented in API ``criteria`` block) ---
MIN_QUOTE_VOLUME_USDT = 5_000_000.0
MIN_DAILY_RANGE_PCT = 1.2
MAX_DAILY_RANGE_PCT = 28.0
IDEAL_DAILY_RANGE_PCT = 5.5
MIN_ABS_PRICE_CHANGE_PCT = 0.35
MAX_ABS_PRICE_CHANGE_PCT = 22.0
DEFAULT_BAND_SPAN_PCT = 7.0
DEFAULT_TARGET_LINES = 10
MIN_VIABLE_LINES = 6
MAX_SCAN_CANDIDATES = 100
NOTIONAL_BUFFER = 1.12
MIN_SCORE = 42.0


@dataclass
class GridScanCriteria:
    min_quote_volume_usdt: float = MIN_QUOTE_VOLUME_USDT
    min_daily_range_pct: float = MIN_DAILY_RANGE_PCT
    max_daily_range_pct: float = MAX_DAILY_RANGE_PCT
    min_usdt_per_line: float = MIN_USDT_PER_LINE_DEFAULT
    min_line_spacing_pct: float = MIN_LINE_SPACING_PCT_DEFAULT
    band_span_pct: float = DEFAULT_BAND_SPAN_PCT
    min_viable_lines: int = MIN_VIABLE_LINES


def scan_criteria_dict(criteria: GridScanCriteria | None = None) -> dict[str, Any]:
    c = criteria or GridScanCriteria()
    return {
        "minQuoteVolumeUsdt": c.min_quote_volume_usdt,
        "minDailyRangePct": c.min_daily_range_pct,
        "maxDailyRangePct": c.max_daily_range_pct,
        "minUsdtPerLine": c.min_usdt_per_line,
        "minLineSpacingPct": round(c.min_line_spacing_pct * 100.0, 4),
        "bandSpanPct": c.band_span_pct,
        "minViableLines": c.min_viable_lines,
        "gridTradableOnly": True,
        "statusTradingOnly": True,
        "quote": "USDT",
    }


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def daily_range_pct(ticker: dict[str, Any], last_price: float) -> float:
    if last_price <= 0:
        return 0.0
    hi = _f(ticker.get("highPrice"))
    lo = _f(ticker.get("lowPrice"))
    if hi > lo > 0:
        return (hi - lo) / last_price * 100.0
    return abs(_f(ticker.get("priceChangePercent")))


def build_filters_map(exchange_symbols: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for row in exchange_symbols:
        if not isinstance(row, dict):
            continue
        sym = normalize_symbol(str(row.get("symbol") or ""))
        if sym:
            out[sym] = parse_symbol_filters(row)
    return out


def _pick_generator_count(
    *,
    limits: dict[str, float | int | bool | str],
    allocated_capital: float,
    min_notional: float,
    criteria: GridScanCriteria,
) -> int | None:
    max_lines = int(limits.get("maxGeneratorCount") or 2)
    if max_lines < criteria.min_viable_lines:
        return None
    floor_usdt = max(criteria.min_usdt_per_line, min_notional * NOTIONAL_BUFFER)
    cap_lines = int(allocated_capital // floor_usdt) if floor_usdt > 0 else max_lines
    cap_lines = max(criteria.min_viable_lines, cap_lines)
    target = min(DEFAULT_TARGET_LINES, max_lines, cap_lines)
    if target < criteria.min_viable_lines:
        return None
    return target


@dataclass
class SymbolScanResult:
    symbol: str
    base_asset: str
    score: float
    last_price: float
    quote_volume_24h: float
    price_change_percent: float
    daily_range_pct: float
    generator_upper: float
    generator_lower: float
    generator_count: int
    max_generator_count: int
    usdt_per_line: float
    line_spacing_pct: float
    min_notional: float
    band_span_pct: float
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    def to_api_dict(self, rank: int) -> dict[str, Any]:
        return {
            "rank": rank,
            "symbol": self.symbol,
            "baseAsset": self.base_asset,
            "score": round(self.score, 1),
            "lastPrice": self.last_price,
            "quoteVolume24h": round(self.quote_volume_24h, 2),
            "priceChangePercent": round(self.price_change_percent, 3),
            "dailyRangePct": round(self.daily_range_pct, 3),
            "generatorUpper": self.generator_upper,
            "generatorLower": self.generator_lower,
            "generatorCount": self.generator_count,
            "maxGeneratorCount": self.max_generator_count,
            "usdtPerLine": round(self.usdt_per_line, 2),
            "lineSpacingPct": round(self.line_spacing_pct, 4),
            "minNotional": self.min_notional,
            "bandSpanPct": self.band_span_pct,
            "reasons": self.reasons,
            "checks": self.checks,
        }


def evaluate_symbol_for_grid(
    *,
    symbol: str,
    base_asset: str,
    ticker: dict[str, Any],
    filters: dict[str, float],
    allocated_capital: float,
    criteria: GridScanCriteria | None = None,
) -> SymbolScanResult | None:
    """Return scored result or None if the symbol fails hard gates."""
    c = criteria or GridScanCriteria()
    sym = normalize_symbol(symbol)
    if not sym or not is_grid_tradable_base(base_asset):
        return None

    last = _f(ticker.get("lastPrice") or ticker.get("weightedAvgPrice"))
    if last <= 0:
        return None

    qvol = _f(ticker.get("quoteVolume"))
    if qvol < c.min_quote_volume_usdt:
        return None

    rng_pct = daily_range_pct(ticker, last)
    if rng_pct < c.min_daily_range_pct or rng_pct > c.max_daily_range_pct:
        return None

    chg = _f(ticker.get("priceChangePercent"))
    if abs(chg) < MIN_ABS_PRICE_CHANGE_PCT or abs(chg) > MAX_ABS_PRICE_CHANGE_PCT:
        return None

    tick = float(filters.get("tick_size") or 0)
    min_notional = float(filters.get("min_notional") or 5.0)

    try:
        lo, hi = band_from_mark_span(last, span_pct=c.band_span_pct, tick_size=tick)
    except ValueError:
        return None

    limits = compute_grid_line_limits(
        generator_upper=hi,
        generator_lower=lo,
        allocated_capital=allocated_capital,
    )
    if not limits.get("valid"):
        return None

    gcount = _pick_generator_count(
        limits=limits,
        allocated_capital=allocated_capital,
        min_notional=min_notional,
        criteria=c,
    )
    if gcount is None:
        return None

    try:
        validate_grid_economics(
            generator_upper=hi,
            generator_lower=lo,
            generator_count=gcount,
            allocated_capital=allocated_capital,
            min_usdt_per_line=c.min_usdt_per_line,
            min_line_spacing_pct=c.min_line_spacing_pct,
        )
    except ValueError:
        return None

    per_line = allocated_capital / gcount
    if per_line < max(c.min_usdt_per_line, min_notional * NOTIONAL_BUFFER):
        return None

    mid = (hi + lo) / 2.0
    span_pct = (hi - lo) / mid if mid > 0 else 0.0
    spacing_pct = span_pct / max(gcount - 1, 1)

    reasons: list[str] = []
    if qvol >= 50_000_000:
        reasons.append("سيولة ممتازة (حجم USDT 24س)")
    elif qvol >= 15_000_000:
        reasons.append("سيولة قوية للشبكة")
    else:
        reasons.append("سيولة كافية لأوامر الشبكة")

    if abs(chg - IDEAL_DAILY_RANGE_PCT) <= 3.0:
        reasons.append("تذبذب يومي مناسب لأرباح الشبكة")
    elif rng_pct >= 3.0:
        reasons.append("نطاق يومي يسمح بتحرك بين الخطوط")

    if int(limits.get("maxGeneratorCount") or 0) >= 12:
        reasons.append("عدد خطوط مرن ضمن قواعد المشروع")
    else:
        reasons.append("اقتصاديات خطوط ضمن الحدود الآمنة")

    if per_line >= c.min_usdt_per_line * 1.5:
        reasons.append("رأس مال لكل خط مريح فوق الحد الأدنى")

    checks = {
        "gridTradable": True,
        "economicsOk": True,
        "minNotionalOk": per_line >= min_notional * NOTIONAL_BUFFER,
        "spacingOk": spacing_pct >= c.min_line_spacing_pct,
        "bandCalibrated": True,
    }

    score = _composite_score(
        quote_volume=qvol,
        range_pct=rng_pct,
        change_pct=chg,
        max_lines=int(limits.get("maxGeneratorCount") or 0),
        usdt_per_line=per_line,
        min_usdt=c.min_usdt_per_line,
    )
    if score < MIN_SCORE:
        return None

    return SymbolScanResult(
        symbol=sym,
        base_asset=base_asset.strip().upper(),
        score=score,
        last_price=last,
        quote_volume_24h=qvol,
        price_change_percent=chg,
        daily_range_pct=rng_pct,
        generator_upper=hi,
        generator_lower=lo,
        generator_count=gcount,
        max_generator_count=int(limits.get("maxGeneratorCount") or gcount),
        usdt_per_line=per_line,
        line_spacing_pct=spacing_pct * 100.0,
        min_notional=min_notional,
        band_span_pct=c.band_span_pct,
        reasons=reasons[:5],
        checks=checks,
    )


def _composite_score(
    *,
    quote_volume: float,
    range_pct: float,
    change_pct: float,
    max_lines: int,
    usdt_per_line: float,
    min_usdt: float,
) -> float:
    vol_score = min(30.0, max(0.0, math.log10(max(quote_volume, 1.0)) - 6.0) * 8.0)
    range_dist = abs(range_pct - IDEAL_DAILY_RANGE_PCT)
    range_score = max(0.0, 22.0 - range_dist * 2.2)
    chg_score = max(0.0, 18.0 - abs(abs(change_pct) - IDEAL_DAILY_RANGE_PCT) * 1.4)
    line_score = min(18.0, max_lines * 1.2)
    cap_score = min(12.0, (usdt_per_line / max(min_usdt, 1.0)) * 3.0)
    return vol_score + range_score + chg_score + line_score + cap_score


def rank_grid_symbol_suggestions(
    *,
    exchange_symbols: list[dict[str, Any]],
    tickers: list[dict[str, Any]],
    allocated_capital: float,
    quote: str = "USDT",
    limit: int = 12,
    criteria: GridScanCriteria | None = None,
    exclude_symbols: frozenset[str] | None = None,
) -> tuple[list[SymbolScanResult], int]:
    """
    Scan tradable USDT pairs; return top ``limit`` by score and count of rejects.
    """
    c = criteria or GridScanCriteria()
    quote_u = quote.strip().upper()
    filt_map = build_filters_map(exchange_symbols)
    ticker_by_sym: dict[str, dict[str, Any]] = {}
    for row in tickers:
        if isinstance(row, dict):
            sym = normalize_symbol(str(row.get("symbol") or ""))
            if sym:
                ticker_by_sym[sym] = row

    candidates: list[tuple[float, str, str, dict[str, Any]]] = []
    for row in exchange_symbols:
        if not isinstance(row, dict):
            continue
        sym = normalize_symbol(str(row.get("symbol") or ""))
        if not sym.endswith(quote_u):
            continue
        if str(row.get("status", "")).upper() != "TRADING":
            continue
        base = str(row.get("baseAsset") or sym[: -len(quote_u)] or sym)
        if quote_u == "USDT" and not is_grid_tradable_base(base):
            continue
        if exclude_symbols and sym in exclude_symbols:
            continue
        tick = ticker_by_sym.get(sym)
        if not tick:
            continue
        qvol = _f(tick.get("quoteVolume"))
        candidates.append((qvol, sym, base, tick))

    candidates.sort(key=lambda x: x[0], reverse=True)
    candidates = candidates[:MAX_SCAN_CANDIDATES]

    results: list[SymbolScanResult] = []
    rejected = 0
    for _qvol, sym, base, tick in candidates:
        flt = filt_map.get(sym)
        if not flt:
            rejected += 1
            continue
        hit = evaluate_symbol_for_grid(
            symbol=sym,
            base_asset=base,
            ticker=tick,
            filters=flt,
            allocated_capital=allocated_capital,
            criteria=c,
        )
        if hit is None:
            rejected += 1
            continue
        results.append(hit)

    results.sort(key=lambda r: r.score, reverse=True)
    cap = max(1, min(int(limit), 24))
    return results[:cap], rejected


def suggestions_payload(
    results: list[SymbolScanResult],
    *,
    allocated_capital: float,
    binance_env: str,
    rejected_count: int,
    criteria: GridScanCriteria | None = None,
) -> dict[str, Any]:
    c = criteria or GridScanCriteria()
    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "allocatedCapital": float(allocated_capital),
        "bandSpanPct": c.band_span_pct,
        "binanceEnv": binance_env,
        "rejectedCount": rejected_count,
        "scannedCandidates": min(MAX_SCAN_CANDIDATES, rejected_count + len(results)),
        "criteria": scan_criteria_dict(c),
        "suggestions": [r.to_api_dict(i + 1) for i, r in enumerate(results)],
    }
