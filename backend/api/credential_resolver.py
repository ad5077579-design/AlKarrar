"""Resolve Binance API keys from server ``.env`` only (BFF — no UI key storage for trading)."""

from __future__ import annotations

from backend.core.binance_env import BinanceSpotEnv, is_paper_env
from backend.core.binance_key_probe import resolve_binance_env_for_keys
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

    Keys are read **only** from ``EngineSettings`` / ``.env``.
    When ``ALKARRAR_AUTO_DETECT_BINANCE_ENV=true`` (default), ``env`` is probed via REST
    (demo / mainnet / testnet) — ``BINANCE_ENV`` is only a hint, not a hard lock.
    """
    _ = bot_id
    eng = EngineSettings()
    legacy = bool(getattr(eng, "binance_legacy_futures_testnet", False))
    hint = eng.resolved_binance_env()
    k = eng.binance_api_key.strip() if eng.binance_api_key else ""
    s = eng.binance_api_secret.strip() if eng.binance_api_secret else ""
    if k and s:
        env = await resolve_binance_env_for_keys(k, s, hint=hint)
        return k, s, env, legacy
    return None, None, hint, legacy


def exchange_testnet_flag(env: BinanceSpotEnv) -> bool:
    """Dashboard ``exchangeTestnet`` / ``binanceTestnet`` (non-mainnet)."""
    return is_paper_env(env)
