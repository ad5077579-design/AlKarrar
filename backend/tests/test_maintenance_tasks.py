"""SQLite retention purge for audit + trade fills."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.financial

from backend.api.maintenance_tasks import purge_old_audit_logs, purge_old_trade_fills
from backend.database import async_session_factory
from backend.database.models.bot_audit_log import BotAuditLog
from backend.database.models.trade_fill import TradeFill


def test_purge_old_audit_and_fills() -> None:
    async def _run() -> None:
        bid = f"test_maint_purge_{uuid.uuid4().hex[:12]}"
        old = datetime.now(timezone.utc) - timedelta(days=40)
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        tid_old = f"maint-old-{uuid.uuid4().hex}"
        tid_recent = f"maint-recent-{uuid.uuid4().hex}"
        async with async_session_factory() as session:
            session.add(
                BotAuditLog(
                    bot_id=bid,
                    symbol="DOGEUSDT",
                    timestamp=old,
                    event_type="GRID_SHIFT",
                    mark_price=0.1,
                    realized_usdt=0.0,
                    details={},
                )
            )
            session.add(
                BotAuditLog(
                    bot_id=bid,
                    symbol="DOGEUSDT",
                    timestamp=recent,
                    event_type="GRID_SHIFT",
                    mark_price=0.1,
                    realized_usdt=0.0,
                    details={},
                )
            )
            session.add(
                TradeFill(
                    bot_id=bid,
                    exchange_trade_id=tid_old,
                    order_id="1",
                    symbol="DOGEUSDT",
                    side="BUY",
                    price=0.1,
                    quantity=10.0,
                    traded_at=old,
                    created_at=old,
                )
            )
            session.add(
                TradeFill(
                    bot_id=bid,
                    exchange_trade_id=tid_recent,
                    order_id="2",
                    symbol="DOGEUSDT",
                    side="BUY",
                    price=0.1,
                    quantity=10.0,
                    traded_at=recent,
                    created_at=recent,
                )
            )
            await session.commit()

        n_audit = await purge_old_audit_logs(retention_days=7)
        n_fill = await purge_old_trade_fills(retention_days=30)
        assert n_audit >= 1
        assert n_fill >= 1

        async with async_session_factory() as session:
            audits = (
                await session.execute(select(BotAuditLog).where(BotAuditLog.bot_id == bid))
            ).scalars().all()
            fills = (
                await session.execute(select(TradeFill).where(TradeFill.bot_id == bid))
            ).scalars().all()
        assert len(audits) == 1
        assert len(fills) == 1

    asyncio.run(_run())
