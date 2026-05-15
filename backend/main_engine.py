"""
AlKarrar Pro — trade engine core (Binance connectivity, DCA grid, hedging, risk).

Naming contract (do not rename): generatorUpper, generatorLower, generatorCount.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

try:
    import ccxt  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "ccxt is required. Install dependencies: pip install -r requirements.txt"
    ) from exc


from backend.project_paths import project_root


def _setup_logging() -> None:
    data_dir = project_root() / "data" / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "engine.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


class EngineSettings(BaseSettings):
    """Runtime configuration from environment (no secrets in repo)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = True
    #: If ``True`` with ``binance_testnet``, use legacy ``testnet.binancefuture.com`` instead of Unified Demo ``demo-fapi.binance.com``.
    binance_legacy_futures_testnet: bool = False


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
    max_daily_loss_usdt: float = Field(ge=0)
    max_leverage: float = Field(default=1.0, gt=0, le=125)


class HedgeLeg(BaseModel):
    symbol: str
    side: Literal["long", "short"]
    target_notional_usdt: float = Field(gt=0)


class HedgeBasket(BaseModel):
    """Spot/futures or pair hedge: primary + offset legs."""

    primary: HedgeLeg
    hedge: HedgeLeg
    hedge_ratio: float = Field(default=1.0, gt=0, le=2.0, description="Hedge size as fraction of primary.")


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


class RiskManager:
    """Pre-trade checks; extend with DB-backed counters for production."""

    def __init__(self, limits: RiskLimits) -> None:
        self._limits = limits
        self._state = RiskState()
        self._log = logging.getLogger("risk")

    @property
    def state(self) -> RiskState:
        return self._state

    def register_fill_pnl(self, delta_usdt: float) -> None:
        self._state.realized_pnl_today_usdt += delta_usdt

    def set_open_notional(self, value: float) -> None:
        self._state.open_notional_usdt = max(0.0, value)

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


class BinanceSession:
    """Thin ccxt wrapper with testnet / prod selection."""

    def __init__(self, settings: EngineSettings) -> None:
        self._log = logging.getLogger("binance")
        opts: dict[str, Any] = {
            "apiKey": settings.binance_api_key or None,
            "secret": settings.binance_api_secret or None,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
        self.exchange = ccxt.binance(opts)
        if settings.binance_testnet:
            self.exchange.set_sandbox_mode(True)
        self._settings = settings

    def assert_credentials(self) -> None:
        if not (self._settings.binance_api_key and self._settings.binance_api_secret):
            raise ValueError("BINANCE_API_KEY and BINANCE_API_SECRET must be set for private endpoints")

    def load_markets(self) -> dict[str, Any]:
        return self.exchange.load_markets()

    def fetch_time_ms(self) -> int:
        return int(self.exchange.fetch_time())

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        return self.exchange.fetch_ticker(symbol)

    def fetch_balance(self) -> dict[str, Any]:
        self.assert_credentials()
        return self.exchange.fetch_balance()


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
    # simple log spacing toward lower bound (more density near generatorLower)
    import math

    weights = [math.exp(i / (n - 1)) for i in range(n)]
    s = sum(weights)
    acc = 0.0
    out: list[float] = []
    for w in weights:
        acc += w
        t = acc / s
        out.append(lo + (hi - lo) * t)
    return out


def hedge_notionals(basket: HedgeBasket) -> tuple[float, float]:
    """Returns (primary_notional, hedge_notional) in USDT terms as configured."""
    p = basket.primary.target_notional_usdt
    h = p * basket.hedge_ratio
    return p, h


class TradeEngine:
    """
    Orchestrates connectivity, DCA grid derivation, hedge sizing, and risk gates.

    This module intentionally avoids HTTP servers; wire FastAPI/uvicorn later.
    """

    def __init__(
        self,
        *,
        settings: EngineSettings | None = None,
        risk: RiskLimits | None = None,
    ) -> None:
        _setup_logging()
        self._log = logging.getLogger("engine")
        self.settings = settings or EngineSettings()
        self.risk_limits = risk or RiskLimits(
            max_notional_usdt=5_000.0,
            max_order_notional_usdt=500.0,
            max_daily_loss_usdt=200.0,
        )
        self.risk = RiskManager(self.risk_limits)
        self.session = BinanceSession(self.settings)

    def ping_exchange(self) -> dict[str, Any]:
        """Public connectivity check (no keys required for time)."""
        server_ms = self.session.fetch_time_ms()
        local_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        skew_ms = server_ms - local_ms
        self._log.info("exchange_time_skew_ms=%s", skew_ms)
        return {"server_time_ms": server_ms, "skew_ms": skew_ms}

    def snapshot_ticker(self, symbol: str) -> dict[str, Any]:
        return self.session.fetch_ticker(symbol)

    def snapshot_balance(self) -> dict[str, Any]:
        return self.session.fetch_balance()

    def plan_dca_orders(
        self,
        symbol: str,
        band: GeneratorBand,
        base_allocation: float,
        first_side: Side,
    ) -> list[OrderIntent]:
        """
        Build a passive ladder: alternating sides by level index (template).

        base_allocation: total base asset budget to spread (simple equal split).
        """
        levels = build_dca_levels(band)
        slice_base = base_allocation / len(levels)
        out: list[OrderIntent] = []
        for i, price in enumerate(levels):
            side = first_side if i % 2 == 0 else (Side.SELL if first_side == Side.BUY else Side.BUY)
            out.append(
                OrderIntent(symbol=symbol, side=side, amount_base=slice_base, price=price, tag=f"dca-{i}")
            )
        return out

    def validate_basket_with_risk(self, basket: HedgeBasket, mark_by_symbol: dict[str, float]) -> None:
        """Ensure hedge notionals pass risk caps using mid marks."""
        p_sym = basket.primary.symbol
        h_sym = basket.hedge.symbol
        if p_sym not in mark_by_symbol or h_sym not in mark_by_symbol:
            raise ValueError("missing mark prices for basket symbols")
        p_not, h_not = hedge_notionals(basket)
        if p_not + h_not > self.risk_limits.max_notional_usdt:
            raise ValueError("hedge basket exceeds max_notional_usdt")
        # translate notionals to synthetic intents at mark for gate only
        p_price = mark_by_symbol[p_sym]
        h_price = mark_by_symbol[h_sym]
        primary_intent = OrderIntent(
            symbol=p_sym,
            side=Side.BUY if basket.primary.side == "long" else Side.SELL,
            amount_base=p_not / p_price,
            price=p_price,
            tag="hedge-primary-check",
        )
        hedge_intent = OrderIntent(
            symbol=h_sym,
            side=Side.BUY if basket.hedge.side == "long" else Side.SELL,
            amount_base=h_not / h_price,
            price=h_price,
            tag="hedge-offset-check",
        )
        self.risk.validate_intent(primary_intent, p_price)
        self.risk.validate_intent(hedge_intent, h_price)


def _cli() -> None:
    engine = TradeEngine()
    sub = (sys.argv[1] or "ping").lower() if len(sys.argv) > 1 else "ping"
    if sub == "ping":
        print(engine.ping_exchange())
        return
    if sub == "ticker" and len(sys.argv) > 2:
        print(engine.snapshot_ticker(sys.argv[2]))
        return
    if sub == "balance":
        print({k: v for k, v in engine.snapshot_balance().items() if k in ("total", "free", "used")})
        return
    if sub == "demo":
        band = GeneratorBand(generatorUpper=102.0, generatorLower=98.0, generatorCount=5)
        levels = build_dca_levels(band)
        print("dca_levels", levels)
        basket = HedgeBasket(
            primary=HedgeLeg(symbol="BTC/USDT", side="long", target_notional_usdt=400.0),
            hedge=HedgeLeg(symbol="ETH/USDT", side="short", target_notional_usdt=350.0),
            hedge_ratio=0.35,
        )
        marks = {"BTC/USDT": 65000.0, "ETH/USDT": 3500.0}
        engine.risk.set_open_notional(0.0)
        engine.validate_basket_with_risk(basket, marks)
        print("hedge_notionals_usdt", hedge_notionals(basket))
        return
    print("usage: python -m backend.main_engine [ping|ticker SYMBOL|balance|demo]")


if __name__ == "__main__":
    _cli()
