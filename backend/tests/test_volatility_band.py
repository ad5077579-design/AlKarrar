"""AVB: ATR math, adaptive multipliers, fee gate, span-change threshold."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.financial

from backend.api.volatility_band import (
    AvbConfig,
    adaptive_multipliers,
    atr_pct_from_ohlc,
    band_span_pct,
    build_vol_profile,
    effective_band_span_pct,
    effective_trailing_stop_pct,
    level_spacing_pct_at,
    min_edge_spacing_pct,
    spacing_passes_fee_gate,
    span_change_pct_enough,
)


def test_atr_pct_from_ohlc_stable_series():
    # 20 candles, small range
    n = 25
    highs = [101.0 + i * 0.01 for i in range(n)]
    lows = [99.0 + i * 0.01 for i in range(n)]
    closes = [100.0 + i * 0.01 for i in range(n)]
    atr = atr_pct_from_ohlc(highs, lows, closes, period=14)
    assert atr > 0
    assert atr < 5.0


def test_adaptive_multipliers_clamp():
    cfg = AvbConfig(atr_ref_pct=2.5, span_clamp_min=0.6, span_clamp_max=1.8)
    low_span, _, _ = adaptive_multipliers(1.0, cfg)
    high_span, _, _ = adaptive_multipliers(10.0, cfg)
    assert low_span == pytest.approx(0.6, rel=1e-6)
    assert high_span == pytest.approx(1.8, rel=1e-6)


def test_effective_band_span_scales_with_vol():
    cfg = AvbConfig(base_band_span_pct=7.0, atr_ref_pct=2.5)
    calm = effective_band_span_pct(7.0, 1.25, cfg)
    hot = effective_band_span_pct(7.0, 5.0, cfg)
    assert hot > calm


def test_min_edge_spacing_covers_round_trip_fee():
    edge = min_edge_spacing_pct(taker_fee=0.001, min_profit_margin=0.0005)
    assert edge >= 0.0025


def test_spacing_passes_fee_gate_rejects_tight_grid():
    levels = [100.0, 100.05, 100.10]
    edge = min_edge_spacing_pct(taker_fee=0.001, min_profit_margin=0.0005)
    assert not spacing_passes_fee_gate(levels, 1, min_edge=edge)
    wide = [100.0, 100.5, 101.0]
    assert spacing_passes_fee_gate(wide, 1, min_edge=edge)


def test_level_spacing_pct_at_neighbor_gap():
    levels = [10.0, 10.2, 10.5]
    # min(|10.2-10.0|, |10.5-10.2|) / 10.2
    assert level_spacing_pct_at(levels, 1) == pytest.approx(0.2 / 10.2, rel=1e-6)


def test_span_change_pct_enough():
    assert span_change_pct_enough(7.0, 7.5, 8.0) is False
    assert span_change_pct_enough(7.0, 7.7, 8.0) is True


def test_avb_blocks_recal_when_trailing_active():
    from backend.strategies.alkarrar_pro_shifting_grid import (
        AlKarrarProShiftingGridStrategy,
        LineTrailPhase,
        LineTrailState,
        ShiftingGridRAM,
    )

    s = AlKarrarProShiftingGridStrategy()
    s._running = True
    s._avb_enabled = True
    s._avb_io_busy = False
    s._line_fill_mutex = set()
    s._ram = ShiftingGridRAM(
        generatorUpper=1.1,
        generatorLower=0.9,
        generatorCount=5,
        initialCapital=100.0,
        trailingOffset=0.01,
        compoundingFactor=1.0,
    )
    s._ram.line_trail[0] = LineTrailState(
        phase=LineTrailPhase.trailing,
        tp_level=1.05,
        lock_floor=1.04,
        trail_peak=1.06,
        trailing_audit_done=True,
        exchange_fill_confirmed=True,
    )
    assert s._can_recalibrate_vol_band() is False


def test_build_vol_profile_fields():
    cfg = AvbConfig()
    p = build_vol_profile(
        symbol="DOGEUSDT",
        atr_pct=3.0,
        cfg=cfg,
        base_band_span_pct=7.0,
        base_trailing_stop_pct=0.01,
        candles_used=20,
    )
    d = p.to_api_dict()
    assert d["symbol"] == "DOGEUSDT"
    assert d["effectiveBandSpanPct"] > 0
    assert band_span_pct(1.07, 0.93) > 0
