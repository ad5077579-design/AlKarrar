"""Trade journal: normalize Binance fills, persist, and broadcast to dashboard WS."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.bot_hub import hub
from backend.core.binance_client import BinanceSpotClient
from backend.database.models.trade_fill import TradeFill

_log = logging.getLogger(__name__)


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def normalize_binance_trade_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Map Spot ``GET /api/v3/myTrades`` row to journal dict."""
    if not isinstance(row, dict):
        return None
    trade_id = row.get("id")
    if trade_id is None:
        return None
    try:
        ts_ms = int(row.get("time") or 0)
    except (TypeError, ValueError):
        ts_ms = 0
    if ts_ms <= 0:
        return None
    side = str(row.get("side", "")).upper()
    if side not in ("BUY", "SELL"):
        if "isBuyer" in row:
            side = "BUY" if bool(row.get("isBuyer")) else "SELL"
    if side not in ("BUY", "SELL"):
        return None
    price = _f(row.get("price"))
    qty = _f(row.get("qty"))
    if price <= 0 or qty <= 0:
        return None
    quote = _f(row.get("quoteQty"))
    if quote <= 0:
        quote = price * qty
    return {
        "exchangeTradeId": str(trade_id),
        "orderId": str(row.get("orderId", "")),
        "symbol": str(row.get("symbol", "")).upper().replace("/", ""),
        "side": side,
        "price": price,
        "quantity": qty,
        "quoteQty": quote,
        "realizedPnl": _f(row.get("realizedPnl")),  # spot REST has no realizedPnl; stays 0
        "commission": _f(row.get("commission")),
        "commissionAsset": str(row.get("commissionAsset") or "USDT"),
        "isMaker": bool(row.get("maker")),
        "positionSide": "SPOT",
        "tradedAt": datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat(),
        "tradedAtMs": ts_ms,
    }


def normalize_user_stream_trade(order_payload: dict[str, Any]) -> dict[str, Any] | None:
    """Map ``ORDER_TRADE_UPDATE`` inner ``o`` when ``x`` is ``TRADE``."""
    if not isinstance(order_payload, dict):
        return None
    if str(order_payload.get("x", "")).upper() != "TRADE":
        return None
    trade_id = order_payload.get("t")
    if trade_id is None:
        return None
    try:
        ts_ms = int(order_payload.get("T") or 0)
    except (TypeError, ValueError):
        ts_ms = 0
    if ts_ms <= 0:
        return None
    side = str(order_payload.get("S", "")).upper()
    if side not in ("BUY", "SELL"):
        return None
    price = _f(order_payload.get("L") or order_payload.get("ap"))
    qty = _f(order_payload.get("l"))
    if price <= 0 or qty <= 0:
        return None
    quote = price * qty
    return {
        "exchangeTradeId": str(trade_id),
        "orderId": str(order_payload.get("i", "")),
        "symbol": str(order_payload.get("s", "")).upper().replace("/", ""),
        "side": side,
        "price": price,
        "quantity": qty,
        "quoteQty": quote,
        "realizedPnl": _f(order_payload.get("rp")),
        "commission": _f(order_payload.get("n")),
        "commissionAsset": str(order_payload.get("N") or "USDT"),
        "isMaker": bool(order_payload.get("m")),
        "positionSide": str(order_payload.get("ps") or "BOTH"),
        "tradedAt": datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat(),
        "tradedAtMs": ts_ms,
    }


def commission_to_usdt_row(row: dict[str, Any]) -> float:
    comm = _f(row.get("commission"))
    if comm <= 0:
        return 0.0
    asset = str(row.get("commissionAsset") or "USDT").upper()
    if asset in ("USDT", "USDC", "BUSD", "FDUSD"):
        return comm
    sym = str(row.get("symbol", "")).upper().replace("/", "")
    base = sym[:-4] if sym.endswith("USDT") else sym
    if asset == base:
        px = _f(row.get("price"))
        return comm * px if px > 0 else 0.0
    return 0.0


def summarize_trades(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_quote = 0.0
    total_pnl = 0.0
    total_commission = 0.0
    buy_count = 0
    sell_count = 0
    for r in rows:
        total_quote += _f(r.get("quoteQty"))
        total_pnl += _f(r.get("realizedPnl"))
        total_commission += commission_to_usdt_row(r)
        if str(r.get("side", "")).upper() == "BUY":
            buy_count += 1
        elif str(r.get("side", "")).upper() == "SELL":
            sell_count += 1
    return {
        "count": len(rows),
        "buyCount": buy_count,
        "sellCount": sell_count,
        "totalQuoteVolume": round(total_quote, 4),
        "totalRealizedPnl": round(total_pnl, 6),
        "totalCommission": round(total_commission, 6),
    }


async def upsert_trade_fill(db: AsyncSession, bot_id: str, row: dict[str, Any]) -> None:
    sym = str(row.get("symbol", "")).upper()
    trade_id = str(row.get("exchangeTradeId", ""))
    if not sym or not trade_id:
        return
    traded_at_raw = row.get("tradedAt")
    if isinstance(traded_at_raw, str):
        traded_at = datetime.fromisoformat(traded_at_raw.replace("Z", "+00:00"))
    else:
        traded_at = datetime.now(timezone.utc)
    values = {
        "bot_id": bot_id,
        "exchange_trade_id": trade_id,
        "order_id": str(row.get("orderId", "")),
        "symbol": sym,
        "side": str(row.get("side", "")).upper(),
        "price": _f(row.get("price")),
        "quantity": _f(row.get("quantity")),
        "quote_qty": _f(row.get("quoteQty")),
        "realized_pnl": _f(row.get("realizedPnl")),
        "commission": _f(row.get("commission")),
        "commission_asset": str(row.get("commissionAsset") or "USDT"),
        "is_maker": bool(row.get("isMaker")),
        "position_side": "SPOT",
        "traded_at": traded_at,
        "created_at": datetime.now(timezone.utc),
    }
    stmt = sqlite_insert(TradeFill).values(**values)
    stmt = stmt.on_conflict_do_nothing(index_elements=["bot_id", "exchange_trade_id"])
    await db.execute(stmt)


def trade_fill_to_dict(r: TradeFill) -> dict[str, Any]:
    return {
        "id": r.id,
        "exchangeTradeId": r.exchange_trade_id,
        "orderId": r.order_id,
        "symbol": r.symbol,
        "side": r.side,
        "price": r.price,
        "quantity": r.quantity,
        "quoteQty": r.quote_qty,
        "realizedPnl": r.realized_pnl,
        "commission": r.commission,
        "commissionAsset": r.commission_asset,
        "isMaker": r.is_maker,
        "positionSide": r.position_side,
        "tradedAt": r.traded_at.isoformat() if r.traded_at else None,
        "tradedAtMs": int(r.traded_at.timestamp() * 1000) if r.traded_at else 0,
    }


async def list_trades_from_db(
    db: AsyncSession,
    *,
    bot_id: str,
    symbol: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    stmt = select(TradeFill).where(TradeFill.bot_id == bot_id)
    if symbol:
        stmt = stmt.where(TradeFill.symbol == symbol.upper().replace("/", ""))
    stmt = stmt.order_by(TradeFill.traded_at.desc()).limit(limit)
    result = await db.execute(stmt)
    return [trade_fill_to_dict(r) for r in result.scalars().all()]


def normalize_spot_execution_report(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Map Spot user stream ``executionReport`` when ``x`` is ``TRADE``."""
    if not isinstance(msg, dict) or str(msg.get("e", "")) != "executionReport":
        return None
    if str(msg.get("x", "")).upper() != "TRADE":
        return None
    return normalize_user_stream_trade(msg)


async def sync_trades_from_exchange(
    client: BinanceSpotClient,
    db: AsyncSession,
    *,
    bot_id: str,
    symbol: str,
    limit: int,
    start_time_ms: int | None = None,
) -> list[dict[str, Any]]:
    sym = symbol.upper().replace("/", "")
    raw = await client.get_account_trades(
        symbol=sym,
        limit=limit,
        start_time_ms=start_time_ms,
    )
    normalized: list[dict[str, Any]] = []
    for row in raw:
        n = normalize_binance_trade_row(row)
        if n:
            normalized.append(n)
            await upsert_trade_fill(db, bot_id, n)
    await db.commit()
    normalized.sort(key=lambda x: int(x.get("tradedAtMs") or 0), reverse=True)
    return normalized


async def persist_and_broadcast_spot_execution(bot_id: str, msg: dict[str, Any]) -> None:
    row = normalize_spot_execution_report(msg)
    if not row:
        return
    from backend.database import async_session_factory

    async with async_session_factory() as db:
        await upsert_trade_fill(db, bot_id, row)
        await db.commit()
    await hub.broadcast({"type": "trade", "data": row})
    try:
        from backend.api.grid_manager import grid_manager

        await grid_manager.ingest_trade_row(bot_id, row)
    except Exception:
        _log.debug("grid_manager ingest trade row", exc_info=True)


async def persist_and_broadcast_trade(bot_id: str, order_payload: dict[str, Any]) -> None:
    row = normalize_user_stream_trade(order_payload) or normalize_spot_execution_report(order_payload)
    if not row:
        return
    from backend.database import async_session_factory

    async with async_session_factory() as db:
        await upsert_trade_fill(db, bot_id, row)
        await db.commit()
    await hub.broadcast({"type": "trade", "data": row})
    try:
        from backend.api.grid_manager import grid_manager

        await grid_manager.ingest_trade_row(bot_id, row)
    except Exception:
        _log.debug("grid_manager ingest trade row", exc_info=True)
