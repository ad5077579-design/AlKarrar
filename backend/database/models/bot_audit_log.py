"""Structured audit trail for grid engine decisions (SQLite)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class BotAuditLog(Base):
    __tablename__ = "bot_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bot_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True, default="")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    mark_price: Mapped[float] = mapped_column(Float, default=0.0)
    realized_usdt: Mapped[float] = mapped_column(Float, default=0.0)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
