from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.dependencies import get_db_session
from backend.database.models.bot_settings import BotSettings

router = APIRouter()


@router.get("/{bot_id}")
async def get_settings(bot_id: str, db: AsyncSession = Depends(get_db_session)) -> dict:
    row = await db.scalar(select(BotSettings).where(BotSettings.bot_id == bot_id))
    if row is None:
        return {"bot_id": bot_id, "strategy_key": None, "config_json": {}}
    return {
        "bot_id": row.bot_id,
        "strategy_key": row.strategy_key,
        "config_json": row.config_json,
    }
