"""Binance Spot: account balance + ticker → hub + WebSocket."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from backend.api.bot_hub import hub
from backend.api.binance_pool import get_spot_client
from backend.api.credential_resolver import exchange_testnet_flag, get_binance_keys

_log = logging.getLogger(__name__)

_last_metrics_sig: tuple[float, float, float] | None = None
_last_mark_by_symbol: dict[str, float] = {}


def reset_sync_dedupe() -> None:
    global _last_metrics_sig, _last_mark_by_symbol
    _last_metrics_sig = None
    _last_mark_by_symbol.clear()


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


async def _broadcast_metrics(merged: dict[str, Any], *, ts_iso: str, source: str = "rest") -> None:
    await hub.broadcast(
        {
            "type": "metrics",
            "data": {
                "totalWalletBalance": merged.get("totalWalletBalance"),
                "totalMarginBalance": merged.get("totalMarginBalance"),
                "currentCapital": merged.get("currentCapital"),
                "marginBalance": merged.get("marginBalance"),
                "availableBalance": merged.get("availableBalance"),
                "floatingPnl": merged.get("floatingPnl"),
                "realizedPnl": merged.get("realizedPnl"),
                "syncError": merged.get("syncError"),
                "syncOkAt": merged.get("syncOkAt"),
                "exchangeTestnet": merged.get("exchangeTestnet"),
                "ts": ts_iso,
                "source": source,
            },
        }
    )


def _symbols_for_ticker_refresh() -> list[str]:
    try:
        from backend.api.grid_manager import grid_manager

        syms = grid_manager.active_symbols()
    except Exception:
        syms = []
    if not syms:
        fb = str(hub.state.get("symbol") or "DOGEUSDT").upper().replace("/", "")
        syms = [fb]
    seen: set[str] = set()
    out: list[str] = []
    for s in syms:
        u = s.upper().replace("/", "")
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


async def sync_spot_account_to_hub_once(
    bot_id: str = "default",
    *,
    metrics_source: str = "rest",
) -> None:
    global _last_metrics_sig, _last_mark_by_symbol
    key, secret, env, _legacy = await get_binance_keys(bot_id)
    if not key or not secret:
        return

    client = await get_spot_client(bot_id)
    if client is None:
        return
    try:
        try:
            bal = await client.fetch_account_balance()
            now_iso = datetime.now(timezone.utc).isoformat()
            merged = await hub.merge_account(
                {
                    "totalWalletBalance": bal["totalWalletBalance"],
                    "totalMarginBalance": bal["totalMarginBalance"],
                    "currentCapital": bal["currentCapital"],
                    "marginBalance": bal["marginBalance"],
                    "availableBalance": bal["availableBalance"],
                    "floatingPnl": bal["floatingPnl"],
                    "syncError": "",
                    "syncOkAt": now_iso,
                    "exchangeTestnet": exchange_testnet_flag(env),
                    "binanceEnv": env,
                }
            )
            _last_metrics_sig = (
                bal["totalWalletBalance"],
                bal["totalMarginBalance"],
                bal["availableBalance"],
            )
            await _broadcast_metrics(merged, ts_iso=now_iso, source=metrics_source)
        except Exception as exc:
            msg = str(exc).strip()[:400] or type(exc).__name__
            _log.warning("spot account sync failed: %s", msg)
            await hub.merge_account(
                {"syncError": msg, "exchangeTestnet": exchange_testnet_flag(env), "binanceEnv": env}
            )
            await hub.broadcast({"type": "sync_error", "message": msg})

        symbol_list = _symbols_for_ticker_refresh()
        for symbol in symbol_list:
            try:
                t = await client.fetch_ticker(symbol)
                mark = _f(t.get("lastPrice") or t.get("price") or 0)
                prev = _last_mark_by_symbol.get(symbol, 0.0)
                if mark > 0 and mark != prev:
                    _last_mark_by_symbol[symbol] = mark
                    await hub.merge_room(symbol, {"markPrice": mark})
                    await hub.broadcast_room(
                        symbol,
                        {
                            "type": "mark",
                            "markPrice": mark,
                            "t": int(t.get("time", 0) or 0),
                            "source": "binance_rest",
                        },
                    )
            except Exception as exc:
                msg = str(exc).strip()[:400] or type(exc).__name__
                _log.warning("spot ticker sync failed %s: %s", symbol, msg)

    except Exception as exc:
        msg = str(exc).strip()[:400] or type(exc).__name__
        _log.warning("spot sync client failed: %s", msg)
        await hub.merge_account({"syncError": msg})
        await hub.broadcast({"type": "sync_error", "message": msg})
    finally:
        pass  # pooled client — do not close per tick


async def run_account_sync_loop(stop: asyncio.Event, *, interval_s: float = 4.0) -> None:
    while not stop.is_set():
        await sync_spot_account_to_hub_once()
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue


async def run_account_sync_loop_env(stop: asyncio.Event) -> None:
    if os.getenv("ALKARRAR_ACCOUNT_SYNC", "true").lower() not in ("1", "true", "yes"):
        return
    try:
        interval = float(os.getenv("ALKARRAR_ACCOUNT_SYNC_INTERVAL", "4"))
    except ValueError:
        interval = 2.0
    await run_account_sync_loop(stop, interval_s=max(1.0, interval))


# Back-compat alias (remove when all imports updated)
sync_futures_account_to_hub_once = sync_spot_account_to_hub_once
