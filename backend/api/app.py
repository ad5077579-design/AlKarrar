from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from backend.api import mark_feed, spot_account_sync, spot_user_stream
from backend.api.bot_hub import hub
from backend.api.routers import api_router, dashboard
from backend.api.ws_endpoint import websocket_dashboard
from backend.database import async_session_factory, init_db
from backend.database.models.bot_settings import BotSettings

async def _ensure_default_bot_row() -> None:
    async with async_session_factory() as session:
        row = await session.scalar(select(BotSettings).where(BotSettings.bot_id == "default"))
        if row is None:
            from datetime import datetime, timezone

            session.add(
                BotSettings(
                    bot_id="default",
                    strategy_key="alkarrar_pro_shifting_grid",
                    config_json={
                        "symbol": "DOGEUSDT",
                        "generatorUpper": 0.18,
                        "generatorLower": 0.14,
                        "generatorCount": 5,
                        "initialCapital": 100.0,
                    },
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _ensure_default_bot_row()
    async with async_session_factory() as session:
        row = await session.scalar(select(BotSettings).where(BotSettings.bot_id == "default"))
        if row and row.config_json:
            merged = {**hub.snapshot_defaults(), **dict(row.config_json)}
            await hub.replace_state(merged)
    try:
        await spot_account_sync.sync_spot_account_to_hub_once()
    except Exception:
        import logging

        logging.getLogger(__name__).warning("initial spot sync failed", exc_info=True)
    stop = asyncio.Event()
    mark_task = asyncio.create_task(mark_feed.run_mark_price_feed(stop), name="mark-feed")
    acct_task = asyncio.create_task(spot_account_sync.run_account_sync_loop_env(stop), name="acct-sync")
    user_task = asyncio.create_task(
        spot_user_stream.run_spot_user_stream(stop, bot_id="default"),
        name="user-stream",
    )
    yield
    try:
        from backend.api.grid_manager import grid_manager

        await grid_manager.stop_all()
    except Exception:
        pass
    stop.set()
    mark_task.cancel()
    acct_task.cancel()
    user_task.cancel()
    try:
        await mark_task
    except asyncio.CancelledError:
        pass
    try:
        await acct_task
    except asyncio.CancelledError:
        pass
    try:
        await user_task
    except asyncio.CancelledError:
        pass


def create_app() -> FastAPI:
    app = FastAPI(title="AlKarrar Pro API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api")
    app.include_router(dashboard.emergency_router, prefix="/api", tags=["emergency"])
    app.websocket("/ws")(websocket_dashboard)
    return app


app = create_app()
