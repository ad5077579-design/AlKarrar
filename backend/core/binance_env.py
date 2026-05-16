"""
Binance Spot environment routing (mainnet / testnet / demo).

Documented in repository root ``AGENTS.md`` (جدول REST/WS و ``BINANCE_ENV``).
"""

from __future__ import annotations

from typing import Literal

BinanceSpotEnv = Literal["mainnet", "testnet", "demo"]

_BINANCE_ENVS: frozenset[str] = frozenset({"mainnet", "testnet", "demo"})


def normalize_binance_env(
    raw: str | None,
    *,
    testnet_fallback: bool = True,
) -> BinanceSpotEnv:
    """
    Resolve ``BINANCE_ENV`` (or legacy ``BINANCE_TESTNET`` when unset).

    ``testnet_fallback``: when env string is empty, True → testnet, False → mainnet.
    """
    v = (raw or "").strip().lower()
    if v in _BINANCE_ENVS:
        return v  # type: ignore[return-value]
    return "testnet" if testnet_fallback else "mainnet"


def is_paper_env(env: BinanceSpotEnv) -> bool:
    """Non-production (testnet or demo) — used for ``exchangeTestnet`` / ``binanceTestnet``."""
    return env != "mainnet"


def spot_stream_endpoint(env: BinanceSpotEnv) -> tuple[str, str]:
    """WebSocket market/user stream host and port suffix (``''`` or ``':9443'``)."""
    if env == "testnet":
        return "testnet.binance.vision", ""
    if env == "demo":
        return "demo-stream.binance.com", ":9443"
    return "stream.binance.com", ":9443"


def env_display_label(env: BinanceSpotEnv) -> str:
    if env == "demo":
        return "spot demo (demo-api.binance.com)"
    if env == "testnet":
        return "spot testnet (testnet.binance.vision)"
    return "spot mainnet (api.binance.com)"
