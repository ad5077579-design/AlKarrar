from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class TradeFill(Base):
    """Executed fill from Binance USD-M (user stream or REST ``account/trades``)."""

    __tablename__ = "trade_fills"
    __table_args__ = (UniqueConstraint("bot_id", "exchange_trade_id", name="uq_trade_fill_bot_exchange_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(64), index=True)
    exchange_trade_id: Mapped[str] = mapped_column(String(32), index=True)
    order_id: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float)
    quote_qty: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    commission_asset: Mapped[str] = mapped_column(String(16), default="USDT")
    is_maker: Mapped[bool] = mapped_column(default=False)
    position_side: Mapped[str | None] = mapped_column(String(16), nullable=True)
    traded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
