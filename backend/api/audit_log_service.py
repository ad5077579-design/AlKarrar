"""Append-only structured audit log (async SQLAlchemy)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Final

from sqlalchemy import desc, select

from backend.database import async_session_factory
from backend.database.models.bot_audit_log import BotAuditLog

_log = logging.getLogger(__name__)

TRAILING_STARTED: Final = "TRAILING_STARTED"
TAKE_PROFIT_MARKET: Final = "TAKE_PROFIT_MARKET"
PROFIT_INJECT_EXPAND: Final = "PROFIT_INJECT_EXPAND"
PROFIT_INJECT_COMPOUND: Final = "PROFIT_INJECT_COMPOUND"
GRID_SHIFT: Final = "GRID_SHIFT"
SYSTEM_ERROR: Final = "SYSTEM_ERROR"

ALLOWED_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        TRAILING_STARTED,
        TAKE_PROFIT_MARKET,
        PROFIT_INJECT_EXPAND,
        PROFIT_INJECT_COMPOUND,
        GRID_SHIFT,
        SYSTEM_ERROR,
    }
)


async def write_bot_audit_log(
    bot_id: str,
    event_type: str,
    *,
    symbol: str = "",
    mark_price: float = 0.0,
    realized_usdt: float = 0.0,
    details: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> None:
    if event_type not in ALLOWED_EVENT_TYPES:
        _log.warning("unknown audit event_type=%s", event_type)
    ts = at if at is not None else datetime.now(timezone.utc)
    sym = symbol or ""
    if not sym and details:
        d0 = details.get("symbol") if isinstance(details, dict) else None
        if isinstance(d0, str) and d0.strip():
            sym = d0.strip().upper().replace("/", "")
    row = BotAuditLog(
        bot_id=bot_id or "default",
        symbol=sym,
        timestamp=ts,
        event_type=event_type,
        mark_price=float(mark_price or 0.0),
        realized_usdt=float(realized_usdt or 0.0),
        details=dict(details or {}),
    )
    try:
        async with async_session_factory() as session:
            session.add(row)
            await session.commit()
    except Exception:
        _log.exception("audit_log write failed event=%s bot=%s", event_type, bot_id)


def schedule_bot_audit_event(
    bot_id: str,
    event_type: str,
    *,
    symbol: str = "",
    mark_price: float = 0.0,
    realized_usdt: float = 0.0,
    details: dict[str, Any] | None = None,
) -> None:
    """Fire-and-forget from synchronous strategy hot-path."""
    bid = bot_id or "default"

    async def _run() -> None:
        await write_bot_audit_log(
            bid,
            event_type,
            symbol=symbol,
            mark_price=mark_price,
            realized_usdt=realized_usdt,
            details=details,
        )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run(), name=f"audit-{event_type}-{bid}")
    except RuntimeError:
        _log.warning("no running loop; audit skipped event=%s", event_type)


async def list_audit_logs(
    *,
    bot_id: str,
    symbol: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit), 500))
    async with async_session_factory() as session:
        stmt = select(BotAuditLog).where(BotAuditLog.bot_id == bot_id)
        sym_f = symbol.strip().upper().replace("/", "") if symbol else ""
        if sym_f:
            stmt = stmt.where(BotAuditLog.symbol == sym_f)
        stmt = stmt.order_by(desc(BotAuditLog.timestamp)).limit(lim)
        result = await session.execute(stmt)
        rows = result.scalars().all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "eventType": r.event_type,
                "symbol": r.symbol or "",
                "markPrice": r.mark_price,
                "realizedUsdt": r.realized_usdt,
                "details": r.details or {},
            }
        )
    return out
