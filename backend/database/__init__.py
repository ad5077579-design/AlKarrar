"""SQLAlchemy declarative base and async engine (paths via ``project_paths`` only)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from backend.project_paths import data_dir


class Base(DeclarativeBase):
    pass


def _sqlite_url() -> str:
    path = data_dir() / "alkarrar.db"
    return f"sqlite+aiosqlite:///{path.resolve().as_posix()}"


def create_engine() -> AsyncEngine:
    return create_async_engine(_sqlite_url(), echo=False)


engine: AsyncEngine = create_engine()
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _migrate_sqlite_audit_symbol_column(sync_conn) -> None:
    try:
        r = sync_conn.exec_driver_sql("PRAGMA table_info(bot_audit_logs)")
        cols = [row[1] for row in r.fetchall()]
    except Exception:
        return
    if not cols:
        return
    if "symbol" not in cols:
        sync_conn.exec_driver_sql(
            "ALTER TABLE bot_audit_logs ADD COLUMN symbol VARCHAR(32) NOT NULL DEFAULT ''"
        )
    sync_conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_bot_audit_bot_symbol ON bot_audit_logs (bot_id, symbol)"
    )
    sync_conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_bot_audit_symbol ON bot_audit_logs (symbol)")


async def init_db() -> None:
    """Create tables if missing (dev bootstrap; migrations later)."""
    from backend.database import models  # noqa: F401 — register mappers

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sqlite_audit_symbol_column)
