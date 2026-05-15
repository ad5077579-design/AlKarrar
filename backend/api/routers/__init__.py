from __future__ import annotations

from fastapi import APIRouter

from backend.api.routers import bot_status, credentials, dashboard, history, settings

api_router = APIRouter()
api_router.include_router(bot_status.router, prefix="/bots", tags=["bot-status"])
api_router.include_router(dashboard.router, prefix="/bots", tags=["dashboard"])
api_router.include_router(credentials.router, prefix="/bots", tags=["credentials"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])
api_router.include_router(history.router, prefix="/history", tags=["history"])
