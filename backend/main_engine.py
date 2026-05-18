"""
AlKarrar Pro — shared engine types (DCA band, risk, env settings).

Naming contract (do not rename): generatorUpper, generatorLower, generatorCount.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.project_paths import project_root


class EngineSettings(BaseSettings):
    """Runtime configuration from environment (no secrets in repo)."""

    model_config = SettingsConfigDict(
        env_file=str(project_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    binance_api_key: str = ""
    binance_api_secret: str = ""
    #: ``mainnet`` | ``testnet`` | ``demo`` — overrides ``BINANCE_TESTNET`` when set.
    binance_env: str = ""
    binance_testnet: bool = True
    #: Ignored (Spot-only); kept for older .env files.
    binance_legacy_futures_testnet: bool = False

    def resolved_binance_env(self) -> Literal["mainnet", "testnet", "demo"]:
        from backend.core.binance_env import normalize_binance_env

        raw = self.binance_env.strip() if self.binance_env else ""
        return normalize_binance_env(
            raw if raw else None,
            testnet_fallback=bool(self.binance_testnet),
        )


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class GeneratorBand:
    """
    Institutional grid / DCA band. Names are contractual.

    generatorUpper: top of the operational band (quote terms depend on strategy).
    generatorLower: bottom of the band.
    generatorCount: number of discrete levels (including endpoints policy in build_dca_levels).
    """

    generatorUpper: float
    generatorLower: float
    generatorCount: int

    def __post_init__(self) -> None:
        if self.generatorCount < 2:
            raise ValueError("generatorCount must be >= 2")
        if not (self.generatorLower < self.generatorUpper):
            raise ValueError("require generatorLower < generatorUpper")


class RiskLimits(BaseModel):
    max_notional_usdt: float = Field(gt=0, description="Hard cap on absolute exposure (USDT).")
    max_order_notional_usdt: float = Field(gt=0)
    max_daily_loss_usdt: float = Field(default=0.0, ge=0)
    #: Drawdown from peak equity (0–1) that triggers trailing equity stop, e.g. 0.10 = 10%.
    trailing_equity_drawdown_pct: float = Field(default=0.10, ge=0.0, le=1.0)


class OrderIntent(BaseModel):
    symbol: str
    side: Side
    amount_base: float = Field(gt=0)
    price: float | None = None
    reduce_only: bool = False
    tag: str = ""


class RiskState(BaseModel):
    realized_pnl_today_usdt: float = 0.0
    open_notional_usdt: float = 0.0
    peak_equity_usdt: float = 0.0


class RiskManager:
    """Pre-trade checks and trailing equity stop (peak drawdown)."""

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits
        self._state = RiskState()
        self._log = logging.getLogger("risk")

    @property
    def limits(self) -> RiskLimits:
        return self._limits

    @property
    def state(self) -> RiskState:
        return self._state

    def seed_peak_equity(self, equity_usdt: float) -> None:
        self._state.peak_equity_usdt = max(0.0, float(equity_usdt))

    def register_fill_pnl(self, delta_usdt: float) -> None:
        self._state.realized_pnl_today_usdt += delta_usdt

    def set_open_notional(self, value: float) -> None:
        self._state.open_notional_usdt = max(0.0, value)

    def check_trailing_equity_stop(self, current_equity_usdt: float) -> bool:
        """
        Update peak equity; return True if drawdown from peak >= trailing_equity_drawdown_pct.
        """
        eq = max(0.0, float(current_equity_usdt))
        if eq <= 0:
            return False
        peak = self._state.peak_equity_usdt
        if eq > peak:
            self._state.peak_equity_usdt = eq
            return False
        if peak <= 0:
            self._state.peak_equity_usdt = eq
            return False
        limit = self._limits.trailing_equity_drawdown_pct
        if limit <= 0:
            return False
        drawdown = (peak - eq) / peak
        if drawdown >= limit:
            self._log.warning(
                "trailing equity stop triggered: equity=%.4f peak=%.4f drawdown=%.2f%% limit=%.2f%%",
                eq,
                peak,
                drawdown * 100.0,
                limit * 100.0,
            )
            return True
        return False

    def validate_intent(self, intent: OrderIntent, mark_price: float) -> None:
        notional = intent.amount_base * (intent.price or mark_price)
        if notional > self._limits.max_order_notional_usdt:
            raise ValueError("order notional exceeds max_order_notional_usdt")
        projected = self._state.open_notional_usdt + notional
        if projected > self._limits.max_notional_usdt:
            raise ValueError("projected exposure exceeds max_notional_usdt")
        if self._state.realized_pnl_today_usdt <= -abs(self._limits.max_daily_loss_usdt):
            if self._limits.max_daily_loss_usdt > 0:
                raise ValueError("daily loss limit reached")


def build_dca_levels(band: GeneratorBand, mode: Literal["equal", "log"] = "equal") -> list[float]:
    """
    Returns strictly increasing prices from generatorLower to generatorUpper.

    generatorCount includes both endpoints (total levels == generatorCount).
    """
    lo = band.generatorLower
    hi = band.generatorUpper
    n = band.generatorCount
    if n < 2:
        raise ValueError("generatorCount must be >= 2")
    if mode == "equal":
        step = (hi - lo) / (n - 1)
        return [lo + i * step for i in range(n)]
    weights = [math.exp(i / (n - 1)) for i in range(n)]
    s = sum(weights)
    acc = 0.0
    out: list[float] = []
    for w in weights:
        acc += w
        t = acc / s
        out.append(lo + (hi - lo) * t)
    return out


if __name__ == "__main__":
    print("resolved_binance_env:", EngineSettings().resolved_binance_env())
