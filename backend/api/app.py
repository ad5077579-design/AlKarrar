from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.routers import api_router
from backend.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="AlKarrar Pro API", lifespan=lifespan)
    app.include_router(api_router, prefix="/api")
    return app
