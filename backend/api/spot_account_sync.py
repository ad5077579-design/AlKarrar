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
from backend.api.dashboard_meta import apply_credentials_meta

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


async def _broadcast_metrics(
    merged: dict[str, Any],
    *,
    ts_iso: str,
    source: str = "rest",
    bot_id: str = "default",
) -> None:
    """Push live account metrics to all WebSocket clients (Pinia applies via ``metrics``)."""
    snap = dict(merged)
    await apply_credentials_meta(bot_id, snap)
    await hub.broadcast(
        {
            "type": "metrics",
            "data": {
                "totalWalletBalance": snap.get("totalWalletBalance"),
                "totalMarginBalance": snap.get("totalMarginBalance"),
                "currentCapital": snap.get("currentCapital"),
                "marginBalance": snap.get("marginBalance"),
                "availableBalance": snap.get("availableBalance"),
                "floatingPnl": snap.get("floatingPnl"),
                "realizedPnl": snap.get("realizedPnl"),
                "syncError": snap.get("syncError", ""),
                "syncOkAt": snap.get("syncOkAt", ""),
                "exchangeTestnet": snap.get("exchangeTestnet"),
                "binanceEnv": snap.get("binanceEnv", ""),
                "credentialsConfigured": snap.get("credentialsConfigured"),
                "binanceApiKeyPreview": snap.get("binanceApiKeyPreview", ""),
                "peakEquityUsdt": snap.get("peakEquityUsdt"),
                "currentDrawdownPct": snap.get("currentDrawdownPct"),
                "trailingEquityStopEnabled": snap.get("trailingEquityStopEnabled"),
                "trailingEquityDrawdownLimitPct": snap.get("trailingEquityDrawdownLimitPct"),
                "trailingEquityStopTriggered": snap.get("trailingEquityStopTriggered"),
                "reinjectedRealizedUsdt": snap.get("reinjectedRealizedUsdt"),
                "balanceSource": snap.get("balanceSource", ""),
                "ts": ts_iso,
                "source": source,
            },
        }
    )


def _symbols_for_ticker_refresh() -> list[str]:
    try:
        from backend.api.grid_manager import grid_manager

        syms = list(grid_manager.active_symbols())
    except Exception:
        syms = []
    focus = str(hub.last_focus_symbol or hub.state.get("symbol") or "DOGEUSDT").upper().replace("/", "")
    if focus and focus not in syms:
        syms.insert(0, focus)
    if not syms:
        syms = [focus or "DOGEUSDT"]
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
        now_iso = datetime.now(timezone.utc).isoformat()
        await hub.merge_account(
            {
                "syncError": "لا توجد مفاتيح في .env — عيّن BINANCE_API_KEY و BINANCE_API_SECRET",
                "syncOkAt": "",
                "balanceSource": "",
            }
        )
        err_snap = dict(hub.state)
        await apply_credentials_meta(bot_id, err_snap)
        await _broadcast_metrics(err_snap, ts_iso=now_iso, source="config", bot_id=bot_id)
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
            equity = max(
                float(merged.get("totalWalletBalance") or merged.get("currentCapital") or 0.0),
                0.0,
            )
            from backend.api.portfolio_risk import portfolio_risk, risk_metrics_snapshot

            portfolio_risk.check_trailing_equity_stop(equity)
            risk_patch = risk_metrics_snapshot(equity_usdt=equity)
            risk_patch["balanceSource"] = "binance_spot_live"
            merged = await hub.merge_account({**merged, **risk_patch})
            await _broadcast_metrics(merged, ts_iso=now_iso, source=metrics_source, bot_id=bot_id)
        except Exception as exc:
            msg = str(exc).strip()[:400] or type(exc).__name__
            _log.warning("spot account sync failed: %s", msg)
            merged = await hub.merge_account(
                {
                    "syncError": msg,
                    "syncOkAt": "",
                    "balanceSource": "",
                    "exchangeTestnet": exchange_testnet_flag(env),
                    "binanceEnv": env,
                }
            )
            await apply_credentials_meta(bot_id, merged)
            await _broadcast_metrics(merged, ts_iso=now_iso, source="sync_error", bot_id=bot_id)
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
        merged = await hub.merge_account(
            {"syncError": msg, "syncOkAt": "", "balanceSource": ""}
        )
        await apply_credentials_meta(bot_id, merged)
        now_iso = datetime.now(timezone.utc).isoformat()
        await _broadcast_metrics(merged, ts_iso=now_iso, source="sync_error", bot_id=bot_id)
        await hub.broadcast({"type": "sync_error", "message": msg})
    finally:
        pass  # pooled client — do not close per tick


async def run_account_sync_loop(
    stop: asyncio.Event,
    *,
    interval_s: float = 4.0,
    bot_id: str = "default",
) -> None:
    while not stop.is_set():
        await sync_spot_account_to_hub_once(bot_id)
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
