from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/{bot_id}/status")
async def bot_status(bot_id: str) -> dict[str, str]:
    return {"bot_id": bot_id, "state": "unknown"}
