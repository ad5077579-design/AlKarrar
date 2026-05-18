"""Auto-resume active grids from SQLite snapshots after API restart."""

from __future__ import annotations

import logging
import os

from backend.api.grid_snapshot_store import (
    build_grid_settings_from_snapshot,
    disable_stale_resume_snapshots,
    list_resumable_snapshots,
    snapshot_matches_credentials,
)
from backend.core.binance_key_probe import auto_detect_enabled, credentials_fingerprint

_log = logging.getLogger(__name__)


def auto_resume_enabled() -> bool:
    raw = str(os.getenv("ALKARRAR_GRID_AUTO_RESUME", "true")).strip().lower()
    return raw not in ("0", "false", "no", "off")


async def reconcile_snapshots_for_current_credentials(*, bot_id: str = "default") -> list[str]:
    """Disable auto-resume on snapshots that do not match detected env + API keys."""
    from backend.api.credential_resolver import get_binance_keys

    k1, k2, env, _ = await get_binance_keys(bot_id)
    if not (k1 and k2):
        return []
    fp = credentials_fingerprint(k1, k2)
    disabled = await disable_stale_resume_snapshots(
        bot_id,
        binance_env=env,
        credentials_fingerprint=fp,
    )
    if disabled:
        _log.warning(
            "disabled stale grid resume for %s (env=%s auto_detect=%s)",
            ", ".join(disabled),
            env,
            auto_detect_enabled(),
        )
    return disabled


async def resume_grids_after_startup(*, bot_id: str = "default") -> list[str]:
    """
    Load ``shifting_grid_snapshots`` with ``autoResume`` and start each grid.
    Returns symbols successfully resumed.
    """
    if not auto_resume_enabled():
        _log.info("grid auto-resume disabled (ALKARRAR_GRID_AUTO_RESUME)")
        return []

    from backend.api.credential_resolver import get_binance_keys
    from backend.api.grid_manager import grid_manager

    k1, k2, env, _ = await get_binance_keys(bot_id)
    if not (k1 and k2):
        _log.info("grid auto-resume: no API keys")
        return []

    await reconcile_snapshots_for_current_credentials(bot_id=bot_id)
    fp = credentials_fingerprint(k1, k2)

    rows = await list_resumable_snapshots(bot_id)
    if not rows:
        _log.info("grid auto-resume: no snapshots flagged for resume")
        return []

    resumed: list[str] = []
    for row in rows:
        sym = str(row.get("symbol") or "")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if not snapshot_matches_credentials(
            payload,
            binance_env=env,
            credentials_fingerprint=fp,
        ):
            _log.warning("grid auto-resume: skip %s (env/key mismatch)", sym)
            continue
        settings = build_grid_settings_from_snapshot(row)
        if not settings:
            _log.warning("grid auto-resume: skip invalid snapshot symbol=%s", sym)
            continue
        settings["binanceEnv"] = env
        settings["credentialsFingerprint"] = fp
        if sym in grid_manager.active_symbols():
            _log.info("grid auto-resume: %s already running", sym)
            resumed.append(sym)
            continue
        try:
            await grid_manager.start(bot_id, settings, resume=True)
            resumed.append(sym)
            _log.info("grid auto-resume: started %s env=%s", sym, env)
        except Exception:
            _log.exception("grid auto-resume failed symbol=%s", sym)
    return resumed
