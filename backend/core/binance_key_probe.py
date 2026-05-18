"""
Detect Binance Spot environment (mainnet | testnet | demo) from API keys via REST probe.

No manual ``BINANCE_ENV`` switch required when ``ALKARRAR_AUTO_DETECT_BINANCE_ENV=true``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

from backend.core.binance_client import BinanceSpotClient
from backend.core.binance_env import BinanceSpotEnv, env_display_label

_log = logging.getLogger(__name__)

_PROBE_ORDER_DEFAULT: tuple[BinanceSpotEnv, ...] = ("demo", "mainnet", "testnet")
_env_cache: dict[str, BinanceSpotEnv] = {}


def auto_detect_enabled() -> bool:
    raw = str(os.getenv("ALKARRAR_AUTO_DETECT_BINANCE_ENV", "true")).strip().lower()
    return raw not in ("0", "false", "no", "off")


def credentials_fingerprint(api_key: str, api_secret: str) -> str:
    """Stable hash for snapshot / resume guards (not reversible)."""
    material = f"{api_key.strip()}:{api_secret.strip()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:32]


def reset_binance_env_probe_cache() -> None:
    _env_cache.clear()


def _is_wrong_env_error(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    if code is not None:
        try:
            if int(code) in (-2015, -2014, 401):
                return True
        except (TypeError, ValueError):
            pass
    msg = str(exc).lower()
    return "invalid api-key" in msg or "-2015" in msg or "api-key format" in msg


def _probe_order(hint: BinanceSpotEnv | None) -> tuple[BinanceSpotEnv, ...]:
    if hint is None:
        return _PROBE_ORDER_DEFAULT
    rest = [e for e in _PROBE_ORDER_DEFAULT if e != hint]
    return (hint, *rest)


async def probe_binance_env(
    api_key: str,
    api_secret: str,
    *,
    hint: BinanceSpotEnv | None = None,
) -> BinanceSpotEnv:
    """
    Signed ``GET /api/v3/account`` on each candidate host until one succeeds.
    """
    key = api_key.strip()
    secret = api_secret.strip()
    if not key or not secret:
        raise ValueError("api_key and api_secret required for probe")

    last_wrong: BaseException | None = None
    for env in _probe_order(hint):
        client: BinanceSpotClient | None = None
        try:
            client = await BinanceSpotClient.create_for_env(
                api_key=key,
                api_secret=secret,
                env=env,
            )
            await client.fetch_account()
            if hint is not None and env != hint:
                _log.warning(
                    "BINANCE_ENV hint=%s does not match detected=%s (%s) — using detected",
                    hint,
                    env,
                    env_display_label(env),
                )
            else:
                _log.info("binance env detected: %s (%s)", env, env_display_label(env))
            return env
        except Exception as exc:
            if _is_wrong_env_error(exc):
                last_wrong = exc
                continue
            raise
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass

    raise RuntimeError(
        "Could not detect Binance Spot environment for these API keys "
        "(tried demo, mainnet, testnet). Check key permissions and IP whitelist."
    ) from last_wrong


async def resolve_binance_env_for_keys(
    api_key: str,
    api_secret: str,
    *,
    hint: BinanceSpotEnv | None = None,
) -> BinanceSpotEnv:
    """Cached auto-detect; falls back to ``hint`` when auto-detect is disabled."""
    key = api_key.strip()
    secret = api_secret.strip()
    if not key or not secret:
        if hint is not None:
            return hint
        from backend.core.binance_env import normalize_binance_env

        return normalize_binance_env(None, testnet_fallback=True)

    if not auto_detect_enabled():
        if hint is None:
            from backend.core.binance_env import normalize_binance_env

            return normalize_binance_env(None, testnet_fallback=True)
        return hint

    fp = credentials_fingerprint(key, secret)
    cached = _env_cache.get(fp)
    if cached is not None:
        return cached

    detected = await probe_binance_env(key, secret, hint=hint)
    _env_cache[fp] = detected
    _persist_last_detection(fp, detected)
    return detected


def _persist_last_detection(fingerprint: str, env: BinanceSpotEnv) -> None:
    try:
        from backend.project_paths import data_dir

        path = data_dir() / "binance_env_detected.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "credentialsFingerprint": fingerprint,
                    "binanceEnv": env,
                    "detectedAtMs": int(time.time() * 1000),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        _log.debug("could not write binance_env_detected.json", exc_info=True)


def load_last_detection() -> dict[str, Any] | None:
    try:
        from backend.project_paths import data_dir

        path = data_dir() / "binance_env_detected.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None
