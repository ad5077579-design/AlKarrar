"""Align local SQLite positions with Binance USD-M (exchange is source of truth)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from backend.core.binance_client import BinanceFuturesClient
from backend.database import async_session_factory
from backend.database.models.position import Position

_log = logging.getLogger(__name__)


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


async def reconcile_positions_for_bot(bot_id: str, client: BinanceFuturesClient, symbol: str) -> None:
    """
    Compare open ``positions`` rows for ``bot_id`` + ``symbol`` with ``futures_position_information``.
    Exchange net position wins; extra local open rows are closed.
    """
    sym = symbol.upper().replace("/", "")
    rows = await client.fetch_positions()
    amt = 0.0
    side = "long"
    entry: float | None = None
    upnl: float | None = None
    for p in rows:
        if str(p.get("symbol", "")).upper() != sym:
            continue
        q = _f(p.get("positionAmt", 0))
        if abs(q) < 1e-12:
            continue
        amt = abs(q)
        side = "long" if q > 0 else "short"
        ep = _f(p.get("entryPrice", 0))
        entry = ep if ep > 0 else None
        upnl = _f(p.get("unRealizedProfit", p.get("unrealizedProfit", 0)))
        break

    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        q = select(Position).where(
            Position.bot_id == bot_id,
            Position.symbol == sym,
            Position.closed_at.is_(None),
        )
        db_rows = list((await session.execute(q)).scalars().all())

        if amt <= 0:
            for row in db_rows:
                row.closed_at = now
            await session.commit()
            return

        if not db_rows:
            session.add(
                Position(
                    bot_id=bot_id,
                    symbol=sym,
                    side=side,
                    entry_price=entry,
                    quantity=amt,
                    unrealized_pnl=upnl,
                    opened_at=now,
                )
            )
            await session.commit()
            return

        primary = db_rows[0]
        primary.side = side
        primary.quantity = amt
        primary.entry_price = entry
        primary.unrealized_pnl = upnl
        for extra in db_rows[1:]:
            extra.closed_at = now
        await session.commit()
