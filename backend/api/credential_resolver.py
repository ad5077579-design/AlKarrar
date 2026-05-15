"""Resolve Binance API keys: DB (per bot) first, then ``.env`` / ``EngineSettings``."""

from __future__ import annotations

from sqlalchemy import select

from backend.database import async_session_factory
from backend.database.models.exchange_credential import ExchangeCredential
from backend.main_engine import EngineSettings


def mask_binance_api_key_preview(api_key: str) -> str:
    """Last-four preview for dashboard (never send full keys to the browser)."""
    k = api_key.strip()
    if len(k) <= 6:
        return "****"
    return "****" + k[-4:]


async def get_binance_keys(bot_id: str = "default") -> tuple[str | None, str | None, bool, bool]:
    """
    Return ``(api_key, api_secret, paper, legacy_futures_testnet)``.

    ``paper`` matches the dashboard checkbox / ``BINANCE_TESTNET``: non-mainnet keys.
    When ``paper`` is True, the app defaults to **Unified Binance Demo** (``demo-fapi.binance.com``)
    unless ``BINANCE_LEGACY_FUTURES_TESTNET`` is set in ``.env`` (``legacy_futures_testnet`` True),
    in which case legacy ``testnet.binancefuture.com`` is used.
    """
    eng = EngineSettings()
    legacy = bool(eng.binance_legacy_futures_testnet)
    async with async_session_factory() as session:
        row = await session.scalar(select(ExchangeCredential).where(ExchangeCredential.bot_id == bot_id))
        if row and row.api_key.strip() and row.api_secret.strip():
            return row.api_key.strip(), row.api_secret.strip(), bool(row.testnet), legacy
    k = eng.binance_api_key.strip() if eng.binance_api_key else ""
    s = eng.binance_api_secret.strip() if eng.binance_api_secret else ""
    if k and s:
        return k, s, bool(eng.binance_testnet), legacy
    return None, None, True, legacy
