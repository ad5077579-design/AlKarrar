"""
Binance USD-M: account + mark price via the **same API keys** (DB per bot, else ``.env``).

``fetch_account_balance`` (wallet / margin / available) runs each tick; positions are reconciled
against the exchange. ``fetch_ticker`` is isolated so mark updates can succeed if account fails.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from backend.api.bot_hub import hub
from backend.api.credential_resolver import get_binance_keys
from backend.api.exchange_reconcile import reconcile_positions_for_bot
from backend.core.binance_client import BinanceFuturesClient

_log = logging.getLogger(__name__)

_last_metrics_sig: tuple[float, float, float] | None = None
_last_mark: float | None = None


def reset_sync_dedupe() -> None:
    global _last_metrics_sig, _last_mark
    _last_metrics_sig = None
    _last_mark = None


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


async def sync_futures_account_to_hub_once(
    bot_id: str = "default",
    *,
    metrics_source: str = "rest",
) -> None:
    global _last_metrics_sig, _last_mark
    key, secret, paper, legacy = await get_binance_keys(bot_id)
    if not key or not secret:
        return

    client: BinanceFuturesClient | None = None
    try:
        client = await BinanceFuturesClient.create_for_paper_or_mainnet(
            api_key=key,
            api_secret=secret,
            paper=paper,
            legacy_futures_testnet=legacy,
        )
        symbol = str(hub.state.get("symbol") or "DOGEUSDT").upper().replace("/", "")

        try:
            bal = await client.fetch_account_balance()
            now_iso = datetime.now(timezone.utc).isoformat()
            merged = await hub.merge_state(
                {
                    "totalWalletBalance": bal["totalWalletBalance"],
                    "totalMarginBalance": bal["totalMarginBalance"],
                    "currentCapital": bal["currentCapital"],
                    "marginBalance": bal["marginBalance"],
                    "availableBalance": bal["availableBalance"],
                    "floatingPnl": bal["floatingPnl"],
                    "syncError": "",
                    "syncOkAt": now_iso,
                    "exchangeTestnet": bool(paper),
                }
            )
            _last_metrics_sig = (
                bal["totalWalletBalance"],
                bal["totalMarginBalance"],
                bal["availableBalance"],
            )
            await _broadcast_metrics(merged, ts_iso=now_iso, source=metrics_source)
            try:
                await reconcile_positions_for_bot(bot_id, client, symbol)
            except Exception:
                _log.debug("position reconcile failed", exc_info=True)
        except Exception as exc:
            msg = str(exc).strip()[:400] or type(exc).__name__
            _log.warning("futures account sync failed: %s", msg)
            await hub.merge_state({"syncError": msg, "exchangeTestnet": bool(paper)})
            await hub.broadcast({"type": "sync_error", "message": msg})

        try:
            t = await client.fetch_ticker(symbol)
            mark = _f(t.get("lastPrice") or t.get("price") or t.get("close") or 0)
            if mark > 0 and mark != _last_mark:
                _last_mark = mark
                await hub.merge_state({"markPrice": mark})
                await hub.broadcast(
                    {
                        "type": "mark",
                        "markPrice": mark,
                        "t": int(t.get("time", 0) or 0),
                        "source": "binance_rest",
                    }
                )
        except Exception as exc:
            msg = str(exc).strip()[:400] or type(exc).__name__
            _log.warning("futures ticker sync failed: %s", msg)

    except Exception as exc:
        msg = str(exc).strip()[:400] or type(exc).__name__
        _log.warning("futures sync client failed: %s", msg)
        await hub.merge_state({"syncError": msg})
        await hub.broadcast({"type": "sync_error", "message": msg})
    finally:
        if client is not None:
            await client.aclose()


async def run_account_sync_loop(stop: asyncio.Event, *, interval_s: float = 4.0) -> None:
    while not stop.is_set():
        await sync_futures_account_to_hub_once()
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue


async def run_account_sync_loop_env(stop: asyncio.Event) -> None:
    import os

    if os.getenv("ALKARRAR_ACCOUNT_SYNC", "true").lower() not in ("1", "true", "yes"):
        return
    try:
        interval = float(os.getenv("ALKARRAR_ACCOUNT_SYNC_INTERVAL", "4"))
    except ValueError:
        interval = 4.0
    await run_account_sync_loop(stop, interval_s=max(2.0, interval))
