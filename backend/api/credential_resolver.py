"""Resolve Binance API keys: DB (per bot) first, then ``.env`` / ``EngineSettings``."""



from __future__ import annotations



from sqlalchemy import select



from backend.core.binance_env import BinanceSpotEnv, is_paper_env

from backend.database import async_session_factory

from backend.database.models.exchange_credential import ExchangeCredential

from backend.main_engine import EngineSettings





def mask_binance_api_key_preview(api_key: str) -> str:

    """Last-four preview for dashboard (never send full keys to the browser)."""

    k = api_key.strip()

    if len(k) <= 6:

        return "****"

    return "****" + k[-4:]





async def get_binance_keys(

    bot_id: str = "default",

) -> tuple[str | None, str | None, BinanceSpotEnv, bool]:

    """

    Return ``(api_key, api_secret, env, legacy_futures_testnet)``.



    ``env`` comes from ``BINANCE_ENV`` (or ``BINANCE_TESTNET`` when unset).

    DB credentials with ``binanceTestnet=false`` force ``mainnet``; when true, use

    the resolved env from settings (demo / testnet / mainnet).



    ``legacy_futures_testnet`` is ignored (kept for call-site compatibility).

    """

    eng = EngineSettings()

    legacy = bool(eng.binance_legacy_futures_testnet)

    default_env = eng.resolved_binance_env()

    async with async_session_factory() as session:

        row = await session.scalar(select(ExchangeCredential).where(ExchangeCredential.bot_id == bot_id))

        if row and row.api_key.strip() and row.api_secret.strip():

            env: BinanceSpotEnv = default_env if bool(row.testnet) else "mainnet"

            return row.api_key.strip(), row.api_secret.strip(), env, legacy

    k = eng.binance_api_key.strip() if eng.binance_api_key else ""

    s = eng.binance_api_secret.strip() if eng.binance_api_secret else ""

    if k and s:

        return k, s, default_env, legacy

    return None, None, default_env, legacy





def exchange_testnet_flag(env: BinanceSpotEnv) -> bool:

    """Dashboard ``exchangeTestnet`` / ``binanceTestnet`` (non-mainnet)."""

    return is_paper_env(env)

