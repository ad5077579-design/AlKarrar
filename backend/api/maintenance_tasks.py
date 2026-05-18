"""Background DB purge (audit + trade fills) — no external cron required."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

from backend.database import async_session_factory
from backend.database.models.bot_audit_log import BotAuditLog
from backend.database.models.trade_fill import TradeFill

_log = logging.getLogger(__name__)


def _audit_retention_days() -> int:
    try:
        return max(1, int(os.getenv("ALKARRAR_AUDIT_RETENTION_DAYS", "7")))
    except (TypeError, ValueError):
        return 7


def _trade_fill_retention_days() -> int:
    try:
        return max(1, int(os.getenv("ALKARRAR_TRADE_FILL_RETENTION_DAYS", "30")))
    except (TypeError, ValueError):
        return 30


def _maintenance_interval_s() -> float:
    try:
        hours = float(os.getenv("ALKARRAR_MAINTENANCE_INTERVAL_HOURS", "24"))
        return max(3600.0, hours * 3600.0)
    except (TypeError, ValueError):
        return 24 * 3600.0


async def purge_old_audit_logs(*, retention_days: int | None = None) -> int:
    days = retention_days if retention_days is not None else _audit_retention_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with async_session_factory() as session:
        result = await session.execute(delete(BotAuditLog).where(BotAuditLog.timestamp < cutoff))
        await session.commit()
        return int(result.rowcount or 0)


async def purge_old_trade_fills(*, retention_days: int | None = None) -> int:
    days = retention_days if retention_days is not None else _trade_fill_retention_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with async_session_factory() as session:
        result = await session.execute(delete(TradeFill).where(TradeFill.traded_at < cutoff))
        await session.commit()
        return int(result.rowcount or 0)


async def run_maintenance_once() -> dict[str, int]:
    audit_n = await purge_old_audit_logs()
    fills_n = await purge_old_trade_fills()
    if audit_n or fills_n:
        _log.info("maintenance purge audit=%s trade_fills=%s", audit_n, fills_n)
    return {"audit_deleted": audit_n, "trade_fills_deleted": fills_n}


async def run_maintenance_loop(stop: asyncio.Event) -> None:
    if os.getenv("ALKARRAR_MAINTENANCE_ENABLED", "true").lower() in ("0", "false", "no"):
        _log.info("background maintenance disabled")
        return
    interval = _maintenance_interval_s()
    _log.info("maintenance loop started interval_s=%.0f", interval)
    while not stop.is_set():
        try:
            await run_maintenance_once()
        except Exception:
            _log.exception("maintenance purge failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            continue
