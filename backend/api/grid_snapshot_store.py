"""Persisted shifting-grid snapshots (SQLite ``trader.db``) for crash recovery."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

from backend.project_paths import data_dir

_log = logging.getLogger(__name__)

SNAPSHOT_VERSION = 1


def trader_db_path() -> Path:
    return data_dir() / "trader.db"


async def ensure_snapshot_table(path: Path | None = None) -> None:
    db_path = path or trader_db_path()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS shifting_grid_snapshots (
              bot_id TEXT NOT NULL,
              symbol TEXT NOT NULL,
              payload TEXT NOT NULL,
              updated_ms INTEGER NOT NULL,
              PRIMARY KEY (bot_id, symbol)
            )
            """
        )
        await db.commit()


async def list_resumable_snapshots(
    bot_id: str,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Rows with ``autoResume`` true in payload JSON."""
    await ensure_snapshot_table(path)
    db_path = path or trader_db_path()
    out: list[dict[str, Any]] = []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT bot_id, symbol, payload, updated_ms FROM shifting_grid_snapshots WHERE bot_id = ?",
            (bot_id or "default",),
        )
        rows = await cur.fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if not payload.get("autoResume"):
            continue
        sym = str(row["symbol"] or payload.get("symbol") or "").upper().replace("/", "")
        if not sym:
            continue
        out.append(
            {
                "bot_id": str(row["bot_id"]),
                "symbol": sym,
                "payload": payload,
                "updated_ms": int(row["updated_ms"] or 0),
            }
        )
    out.sort(key=lambda r: int(r.get("updated_ms") or 0), reverse=True)
    return out


def snapshot_matches_credentials(
    payload: dict[str, Any],
    *,
    binance_env: str,
    credentials_fingerprint: str,
) -> bool:
    """Resume only when env + key fingerprint match the live session."""
    if not payload.get("autoResume"):
        return False
    snap_env = str(payload.get("binanceEnv") or "").strip().lower()
    snap_fp = str(payload.get("credentialsFingerprint") or "").strip()
    if not snap_env or not snap_fp:
        return False
    return snap_env == str(binance_env).strip().lower() and snap_fp == credentials_fingerprint


def build_grid_settings_from_snapshot(row: dict[str, Any]) -> dict[str, Any] | None:
    """Merge persisted grid settings + strategy state for ``GridRunner.start``."""
    payload = row.get("payload")
    if not isinstance(payload, dict) or not payload.get("autoResume"):
        return None
    sym = str(row.get("symbol") or payload.get("symbol") or "").upper().replace("/", "")
    gs = dict(payload.get("gridSettings") or {})
    gs.setdefault("symbol", sym)
    for key in (
        "generatorUpper",
        "generatorLower",
        "generatorCount",
        "maxGeneratorCount",
        "initialCapital",
        "allocatedCapital",
        "trailingOffset",
        "trailing_stop_pct",
        "compoundingFactor",
        "profit_injection_mode",
        "max_slippage_pct",
        "dca_mode",
        "lift_above_offset",
        "boundary_epsilon_pct",
    ):
        if key in payload and key not in gs:
            gs[key] = payload[key]
    if "generatorUpper" not in gs or "generatorLower" not in gs:
        return None
    alloc = float(gs.get("allocatedCapital") or gs.get("initialCapital") or payload.get("initialCapital") or 0)
    if alloc <= 0:
        return None
    gs["allocatedCapital"] = alloc
    gs["initialCapital"] = alloc
    gs["resumeFromSnapshot"] = payload
    gs["resumeUpdatedMs"] = int(row.get("updated_ms") or payload.get("updated_ms") or 0)
    gs["resumeSessionStartMs"] = int(
        payload.get("sessionStartMs") or row.get("updated_ms") or (time.time() * 1000) - 3600_000
    )
    return gs


async def disable_stale_resume_snapshots(
    bot_id: str,
    *,
    binance_env: str,
    credentials_fingerprint: str,
    path: Path | None = None,
) -> list[str]:
    """
    Clear ``autoResume`` on snapshots from another env or API key (demo → mainnet safety).
    Returns symbols that were disabled.
    """
    disabled: list[str] = []
    rows = await list_resumable_snapshots(bot_id, path=path)
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        sym = str(row.get("symbol") or "")
        if snapshot_matches_credentials(
            payload,
            binance_env=binance_env,
            credentials_fingerprint=credentials_fingerprint,
        ):
            continue
        await set_auto_resume(bot_id, sym, enabled=False, path=path)
        disabled.append(sym)
    return disabled


async def set_auto_resume(
    bot_id: str,
    symbol: str,
    *,
    enabled: bool,
    path: Path | None = None,
) -> None:
    """Manual grid stop sets ``autoResume`` false so startup does not relaunch."""
    sym = symbol.strip().upper().replace("/", "")
    if not sym:
        return
    await ensure_snapshot_table(path)
    db_path = path or trader_db_path()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT payload FROM shifting_grid_snapshots WHERE bot_id = ? AND symbol = ?",
            (bot_id or "default", sym),
        )
        row = await cur.fetchone()
        if row is None:
            return
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["autoResume"] = bool(enabled)
        await db.execute(
            """
            UPDATE shifting_grid_snapshots
            SET payload = ?, updated_ms = ?
            WHERE bot_id = ? AND symbol = ?
            """,
            (
                json.dumps(payload, separators=(",", ":")),
                int(time.time() * 1000),
                bot_id or "default",
                sym,
            ),
        )
        await db.commit()


async def write_snapshot_payload(
    bot_id: str,
    symbol: str,
    payload: dict[str, Any],
    *,
    path: Path | None = None,
) -> None:
    sym = symbol.strip().upper().replace("/", "")
    await ensure_snapshot_table(path)
    db_path = path or trader_db_path()
    blob = json.dumps(payload, separators=(",", ":"))
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO shifting_grid_snapshots (bot_id, symbol, payload, updated_ms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(bot_id, symbol) DO UPDATE SET
              payload = excluded.payload,
              updated_ms = excluded.updated_ms
            """,
            (bot_id or "default", sym, blob, int(time.time() * 1000)),
        )
        await db.commit()
