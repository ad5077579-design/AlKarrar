from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ExchangeCredential(Base):
    """Per-bot Binance USD-M API keys (dev/local; use a vault in production)."""

    __tablename__ = "exchange_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    api_key: Mapped[str] = mapped_column(Text, default="")
    api_secret: Mapped[str] = mapped_column(Text, default="")
    testnet: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
