"""
Adaptive Volatility Band (AVB): ATR-based span / trailing / lift tuning + fee-aware line spacing gate.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Sequence

from backend.api.spot_realized_ledger import band_from_mark_span, validate_grid_economics
from backend.core.binance_client import BinanceSpotClient

VOL_BAND_RECALIBRATE: str = "VOL_BAND_RECALIBRATE"


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def taker_fee_ratio() -> float:
    raw = (os.getenv("ALKARRAR_SPOT_TAKER_FEE_RATIO") or "").strip()
    try:
        f = float(raw) if raw else 0.001
    except ValueError:
        f = 0.001
    if not (0.0 < f < 0.5):
        return 0.001
    return f


@dataclass(frozen=True)
class AvbConfig:
    enabled: bool = True
    recal_interval_s: float = 900.0
    kline_interval: str = "15m"
    kline_limit: int = 30
    atr_period: int = 14
    atr_ref_pct: float = 2.5
    base_band_span_pct: float = 7.0
    span_clamp_min: float = 0.6
    span_clamp_max: float = 1.8
    recal_span_change_pct: float = 8.0
    min_profit_margin_pct: float = 0.0005

    @classmethod
    def from_env(cls, *, settings: dict[str, Any] | None = None) -> AvbConfig:
        s = settings or {}
        enabled = s.get("adaptiveVolBand")
        if enabled is None:
            enabled = _env_bool("ALKARRAR_GRID_AVB_ENABLED", True)
        else:
            enabled = bool(enabled)
        base_span = s.get("avbBaseBandSpanPct")
        if base_span is None:
            base_span = _env_float("ALKARRAR_GRID_AVB_BASE_BAND_SPAN_PCT", 7.0)
        return cls(
            enabled=enabled,
            recal_interval_s=_env_float("ALKARRAR_GRID_AVB_INTERVAL_S", 900.0),
            kline_interval=str(s.get("avbKlineInterval") or os.getenv("ALKARRAR_GRID_AVB_KLINE_INTERVAL") or "15m"),
            kline_limit=max(20, _env_int("ALKARRAR_GRID_AVB_KLINE_LIMIT", 30)),
            atr_period=max(2, _env_int("ALKARRAR_GRID_AVB_ATR_PERIOD", 14)),
            atr_ref_pct=max(0.1, _env_float("ALKARRAR_GRID_AVB_ATR_REF_PCT", 2.5)),
            base_band_span_pct=max(2.0, min(20.0, float(base_span))),
            span_clamp_min=max(0.3, _env_float("ALKARRAR_GRID_AVB_SPAN_CLAMP_MIN", 0.6)),
            span_clamp_max=max(1.0, _env_float("ALKARRAR_GRID_AVB_SPAN_CLAMP_MAX", 1.8)),
            recal_span_change_pct=max(1.0, _env_float("ALKARRAR_GRID_AVB_RECAL_SPAN_CHANGE_PCT", 8.0)),
            min_profit_margin_pct=max(0.0, _env_float("ALKARRAR_GRID_MIN_PROFIT_MARGIN_PCT", 0.0005)),
        )


@dataclass
class VolProfile:
    symbol: str
    atr_pct: float
    atr_period: int
    atr_ref_pct: float
    vol_ratio: float
    span_multiplier: float
    trail_stop_multiplier: float
    lift_multiplier: float
    effective_band_span_pct: float
    effective_trailing_stop_pct: float
    min_edge_spacing_pct: float
    kline_interval: str
    candles_used: int

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "atrPct": round(self.atr_pct, 4),
            "atrPeriod": self.atr_period,
            "atrRefPct": self.atr_ref_pct,
            "volRatio": round(self.vol_ratio, 4),
            "spanMultiplier": round(self.span_multiplier, 4),
            "trailStopMultiplier": round(self.trail_stop_multiplier, 4),
            "liftMultiplier": round(self.lift_multiplier, 4),
            "effectiveBandSpanPct": round(self.effective_band_span_pct, 4),
            "effectiveTrailingStopPct": round(self.effective_trailing_stop_pct, 6),
            "minEdgeSpacingPct": round(self.min_edge_spacing_pct * 100.0, 4),
            "klineInterval": self.kline_interval,
            "candlesUsed": self.candles_used,
        }


def parse_kline_ohlc(rows: Sequence[Any]) -> tuple[list[float], list[float], list[float]]:
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            highs.append(float(row[2]))
            lows.append(float(row[3]))
            closes.append(float(row[4]))
        except (TypeError, ValueError):
            continue
    return highs, lows, closes


def atr_pct_from_ohlc(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    *,
    period: int = 14,
) -> float:
    """ATR as % of last close (Wilder-style on available window)."""
    n = len(closes)
    p = max(2, int(period))
    if n < p + 1:
        return 0.0
    trs: list[float] = []
    for i in range(1, n):
        h, lo, c_prev = float(highs[i]), float(lows[i]), float(closes[i - 1])
        tr = max(h - lo, abs(h - c_prev), abs(lo - c_prev))
        trs.append(tr)
    if len(trs) < p:
        return 0.0
    atr = sum(trs[:p]) / p
    for tr in trs[p:]:
        atr = (atr * (p - 1) + tr) / p
    last_close = float(closes[-1])
    if last_close <= 0:
        return 0.0
    return (atr / last_close) * 100.0


def vol_ratio(atr_pct: float, atr_ref_pct: float) -> float:
    ref = max(float(atr_ref_pct), 0.1)
    return max(float(atr_pct), 0.0) / ref


def clamp_multiplier(ratio: float, *, lo: float, hi: float) -> float:
    return max(lo, min(hi, ratio))


def adaptive_multipliers(
    atr_pct: float,
    cfg: AvbConfig,
) -> tuple[float, float, float]:
    """span_mult, trail_stop_mult (sqrt), lift_mult."""
    r = vol_ratio(atr_pct, cfg.atr_ref_pct)
    span_m = clamp_multiplier(r, lo=cfg.span_clamp_min, hi=cfg.span_clamp_max)
    trail_m = math.sqrt(span_m)
    lift_m = max(0.5, min(2.0, 0.85 + span_m * 0.15))
    return span_m, trail_m, lift_m


def effective_band_span_pct(base_span_pct: float, atr_pct: float, cfg: AvbConfig) -> float:
    span_m, _, _ = adaptive_multipliers(atr_pct, cfg)
    return max(2.0, min(20.0, float(base_span_pct) * span_m))


def effective_trailing_stop_pct(base_stop_pct: float, atr_pct: float, cfg: AvbConfig) -> float:
    _, trail_m, _ = adaptive_multipliers(atr_pct, cfg)
    return max(0.002, min(0.08, float(base_stop_pct) * trail_m))


def effective_lift_offset(
    base_lift: float,
    band_width: float,
    atr_pct: float,
    cfg: AvbConfig,
) -> float:
    _, _, lift_m = adaptive_multipliers(atr_pct, cfg)
    from_mark = max(band_width * 0.02 * lift_m, band_width * 1.0e-4)
    return max(float(base_lift) * lift_m, from_mark)


def min_edge_spacing_pct(
    *,
    taker_fee: float | None = None,
    min_profit_margin: float = 0.0005,
) -> float:
    fee = taker_fee if taker_fee is not None else taker_fee_ratio()
    return 2.0 * float(fee) + float(min_profit_margin)


def band_span_pct(upper: float, lower: float) -> float:
    mid = (float(upper) + float(lower)) / 2.0
    if mid <= 0:
        return 0.0
    return (float(upper) - float(lower)) / mid * 100.0


def level_spacing_pct_at(levels: Sequence[float], idx: int) -> float:
    if len(levels) < 2:
        return 0.0
    i = int(idx)
    if i < 0 or i >= len(levels):
        return 0.0
    px = float(levels[i])
    if px <= 0:
        return 0.0
    gaps: list[float] = []
    if i > 0:
        gaps.append(abs(px - float(levels[i - 1])))
    if i < len(levels) - 1:
        gaps.append(abs(float(levels[i + 1]) - px))
    if not gaps:
        return 0.0
    return min(gaps) / px


def spacing_passes_fee_gate(
    levels: Sequence[float],
    idx: int,
    *,
    min_edge: float | None = None,
    cfg: AvbConfig | None = None,
) -> bool:
    edge = min_edge if min_edge is not None else min_edge_spacing_pct(
        min_profit_margin=(cfg.min_profit_margin_pct if cfg else 0.0005),
    )
    return level_spacing_pct_at(levels, idx) >= edge


def build_vol_profile(
    *,
    symbol: str,
    atr_pct: float,
    cfg: AvbConfig,
    base_band_span_pct: float,
    base_trailing_stop_pct: float,
    candles_used: int,
) -> VolProfile:
    span_m, trail_m, lift_m = adaptive_multipliers(atr_pct, cfg)
    eff_span = effective_band_span_pct(base_band_span_pct, atr_pct, cfg)
    eff_stop = effective_trailing_stop_pct(base_trailing_stop_pct, atr_pct, cfg)
    edge = min_edge_spacing_pct(min_profit_margin=cfg.min_profit_margin_pct)
    return VolProfile(
        symbol=symbol.upper().replace("/", ""),
        atr_pct=float(atr_pct),
        atr_period=cfg.atr_period,
        atr_ref_pct=cfg.atr_ref_pct,
        vol_ratio=vol_ratio(atr_pct, cfg.atr_ref_pct),
        span_multiplier=span_m,
        trail_stop_multiplier=trail_m,
        lift_multiplier=lift_m,
        effective_band_span_pct=eff_span,
        effective_trailing_stop_pct=eff_stop,
        min_edge_spacing_pct=edge,
        kline_interval=cfg.kline_interval,
        candles_used=candles_used,
    )


def span_change_pct_enough(current_span_pct: float, target_span_pct: float, threshold_pct: float) -> bool:
    cur = max(float(current_span_pct), 1e-9)
    tgt = float(target_span_pct)
    return abs(tgt - cur) / cur * 100.0 >= float(threshold_pct)


def try_band_for_vol_recalibrate(
    *,
    mark: float,
    span_pct: float,
    tick_size: float,
    generator_count: int,
    allocated_capital: float,
) -> tuple[float, float] | None:
    """Return (lower, upper) if economics validate; else None."""
    if mark <= 0:
        return None
    try:
        lo, hi = band_from_mark_span(mark, span_pct=span_pct, tick_size=tick_size)
    except ValueError:
        return None
    try:
        validate_grid_economics(
            generator_upper=hi,
            generator_lower=lo,
            generator_count=generator_count,
            allocated_capital=allocated_capital,
        )
    except ValueError:
        return None
    return lo, hi


async def fetch_vol_profile(
    client: BinanceSpotClient,
    symbol: str,
    *,
    cfg: AvbConfig | None = None,
    base_band_span_pct: float | None = None,
    base_trailing_stop_pct: float = 0.01,
) -> VolProfile:
    c = cfg or AvbConfig.from_env()
    sym = symbol.upper().replace("/", "")
    limit = max(c.atr_period + 5, c.kline_limit)
    raw = await client.get_klines(symbol=sym, interval=c.kline_interval, limit=limit)
    highs, lows, closes = parse_kline_ohlc(raw if isinstance(raw, list) else [])
    atr = atr_pct_from_ohlc(highs, lows, closes, period=c.atr_period)
    if atr <= 0 and closes:
        # fallback: last candle range %
        last = float(closes[-1])
        if last > 0 and highs and lows:
            atr = (float(highs[-1]) - float(lows[-1])) / last * 100.0
    base_span = float(base_band_span_pct if base_band_span_pct is not None else c.base_band_span_pct)
    return build_vol_profile(
        symbol=sym,
        atr_pct=atr,
        cfg=c,
        base_band_span_pct=base_span,
        base_trailing_stop_pct=base_trailing_stop_pct,
        candles_used=len(closes),
    )
