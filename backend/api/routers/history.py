from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db_session
from backend.database.models.order import Order

router = APIRouter()


@router.get("/orders")
async def list_orders(
    bot_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    stmt = select(Order).order_by(Order.created_at.desc()).limit(limit)
    if bot_id:
        stmt = stmt.where(Order.bot_id == bot_id)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "bot_id": r.bot_id,
            "symbol": r.symbol,
            "side": r.side,
            "status": r.status,
            "quantity": r.quantity,
            "price": r.price,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.get("/health")
async def history_health() -> dict[str, str]:
    return {"module": "history", "ts": datetime.now(timezone.utc).isoformat()}
