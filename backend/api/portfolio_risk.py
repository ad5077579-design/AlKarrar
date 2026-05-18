"""Per-grid trailing equity stop (isolated from wallet total) + account-level readouts."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from backend.main_engine import RiskLimits, RiskManager

_log = logging.getLogger(__name__)
_emergency_lock = asyncio.Lock()


def _drawdown_pct_from_env() -> float:
    raw = (os.getenv("ALKARRAR_TRAILING_EQUITY_DRAWDOWN_PCT") or "0.10").strip()
    try:
        v = float(raw)
    except ValueError:
        v = 0.10
    return max(0.0, min(v, 1.0))


def _risk_limits() -> RiskLimits:
    return RiskLimits(
        max_notional_usdt=1_000_000.0,
        max_order_notional_usdt=500_000.0,
        max_daily_loss_usdt=0.0,
        trailing_equity_drawdown_pct=_drawdown_pct_from_env(),
    )


@dataclass
class _GridRiskSlot:
    risk: RiskManager
    emergency_latched: bool = False


_grid_slots: dict[str, _GridRiskSlot] = {}
# Legacy global manager (account-wide metrics only; trailing stop uses per-symbol slots).
portfolio_risk = RiskManager(_risk_limits())


def _norm_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace("/", "")


def _slot(symbol: str) -> _GridRiskSlot:
    sym = _norm_symbol(symbol)
    if sym not in _grid_slots:
        _grid_slots[sym] = _GridRiskSlot(risk=RiskManager(_risk_limits()))
    return _grid_slots[sym]


def reinjected_realized_usdt_from_grids() -> float:
    """Sum cumulative realized USDT across active grid runners (FIFO ledger)."""
    try:
        from backend.api.grid_manager import grid_manager

        total = 0.0
        for runner in grid_manager._by_symbol.values():  # noqa: SLF001
            if not runner.running or not runner._strategy:  # noqa: SLF001
                continue
            ram = getattr(runner._strategy, "_ram", None)  # noqa: SLF001
            if ram is None:
                continue
            total += float(getattr(ram, "cumulative_realized_usdt", 0.0) or 0.0)
        return round(total, 8)
    except Exception:
        return 0.0


def risk_metrics_snapshot(*, equity_usdt: float) -> dict[str, float | bool]:
    """
    Account-level read-only metrics (wallet equity). Per-grid peak/drawdown is on grid status.
    """
    eq = max(0.0, float(equity_usdt))
    peak = max(float(portfolio_risk.state.peak_equity_usdt), eq)
    drawdown = 0.0
    if peak > 0 and eq > 0:
        drawdown = max(0.0, (peak - eq) / peak)
    limit = float(portfolio_risk.limits.trailing_equity_drawdown_pct)
    any_latched = any(s.emergency_latched for s in _grid_slots.values())
    return {
        "peakEquityUsdt": round(peak, 8),
        "currentDrawdownPct": round(drawdown * 100.0, 4),
        "trailingEquityStopEnabled": limit > 0,
        "trailingEquityDrawdownLimitPct": round(limit * 100.0, 2),
        "trailingEquityStopTriggered": any_latched,
        "reinjectedRealizedUsdt": reinjected_realized_usdt_from_grids(),
    }


def grid_risk_metrics_snapshot(*, symbol: str, grid_equity_usdt: float) -> dict[str, float | bool]:
    """Isolated trailing metrics for one symbol's ring-fenced equity."""
    sym = _norm_symbol(symbol)
    slot = _slot(sym)
    eq = max(0.0, float(grid_equity_usdt))
    peak = max(float(slot.risk.state.peak_equity_usdt), eq)
    drawdown = 0.0
    if peak > 0 and eq > 0:
        drawdown = max(0.0, (peak - eq) / peak)
    limit = float(slot.risk.limits.trailing_equity_drawdown_pct)
    return {
        "peakEquityUsdt": round(peak, 8),
        "currentDrawdownPct": round(drawdown * 100.0, 4),
        "trailingEquityStopEnabled": limit > 0,
        "trailingEquityDrawdownLimitPct": round(limit * 100.0, 2),
        "trailingEquityStopTriggered": bool(slot.emergency_latched),
    }


def reset_trailing_equity_baseline(equity_usdt: float) -> None:
    """Legacy: reset account-wide peak (wallet sync)."""
    eq = max(0.0, float(equity_usdt))
    portfolio_risk.seed_peak_equity(eq)


def reset_trailing_equity_baseline_for_grid(symbol: str, grid_equity_usdt: float) -> None:
    """Reset isolated peak for one grid; clears that symbol's emergency latch."""
    sym = _norm_symbol(symbol)
    slot = _slot(sym)
    slot.emergency_latched = False
    slot.risk.seed_peak_equity(max(0.0, float(grid_equity_usdt)))
    _log.info("grid trailing baseline reset %s peak=%.4f USDT", sym, slot.risk.state.peak_equity_usdt)


def trailing_emergency_latched(symbol: str | None = None) -> bool:
    if symbol:
        return bool(_slot(symbol).emergency_latched)
    return any(s.emergency_latched for s in _grid_slots.values())


def clear_grid_risk_slot(symbol: str) -> None:
    sym = _norm_symbol(symbol)
    _grid_slots.pop(sym, None)


async def maybe_trailing_equity_stop_for_grid(
    *,
    symbol: str,
    grid_equity_usdt: float,
    bot_id: str = "default",
) -> bool:
    """
    Per-grid trailing stop: emergency only for ``symbol`` when its isolated equity drawdown hits limit.
    """
    sym = _norm_symbol(symbol)
    if not sym or grid_equity_usdt <= 0:
        return False

    slot = _slot(sym)
    if slot.emergency_latched:
        return True

    if not slot.risk.check_trailing_equity_stop(grid_equity_usdt):
        return False

    async with _emergency_lock:
        slot = _slot(sym)
        if slot.emergency_latched:
            return True
        slot.emergency_latched = True
        peak = slot.risk.state.peak_equity_usdt
        _log.warning(
            "grid trailing equity stop %s: equity=%.4f peak=%.4f drawdown_limit=%.2f%%",
            sym,
            grid_equity_usdt,
            peak,
            slot.risk.limits.trailing_equity_drawdown_pct * 100.0,  # noqa: SLF001
        )
        from backend.api.emergency_service import execute_emergency_stop

        await execute_emergency_stop(bot_id, symbol=sym)
    return True


async def maybe_trailing_equity_stop(*, equity_usdt: float, bot_id: str = "default") -> bool:
    """
    Deprecated wallet-wide stop — evaluates each active grid's isolated equity instead.
    """
    if equity_usdt <= 0:
        return False
    try:
        from backend.api.grid_manager import grid_manager
    except Exception:
        return False

    triggered = False
    for sym, runner in list(grid_manager._by_symbol.items()):  # noqa: SLF001
        if not runner.running:
            continue
        try:
            eq = await runner.compute_grid_equity_usdt()
        except Exception:
            continue
        if await maybe_trailing_equity_stop_for_grid(
            symbol=sym, grid_equity_usdt=eq, bot_id=bot_id
        ):
            triggered = True
    return triggered
