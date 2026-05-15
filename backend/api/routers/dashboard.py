"""Dashboard BFF: snapshot, contractual settings patch, emergency stop."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from binance.exceptions import BinanceAPIException, BinanceRequestException
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api import futures_account_sync
from backend.api.bot_hub import hub
from backend.api.credential_resolver import get_binance_keys, mask_binance_api_key_preview
from backend.api.dependencies import get_db_session
from backend.core.binance_client import BinanceFuturesClient
from backend.database.models.bot_settings import BotSettings

router = APIRouter()
_log = logging.getLogger(__name__)

_KLINES_INTERVALS = frozenset(
    {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"},
)

_DEFAULTS: dict[str, Any] = {
    "symbol": "DOGEUSDT",
    "markPrice": 0.0,
    "generatorUpper": 0.0,
    "generatorLower": 0.0,
    "generatorCount": 5,
    "initialCapital": 100.0,
    "realizedPnl": 0.0,
    "floatingPnl": 0.0,
    "totalWalletBalance": 0.0,
    "totalMarginBalance": 0.0,
    "currentCapital": 0.0,
    "marginBalance": 0.0,
    "availableBalance": 0.0,
    "activeGridLines": 5,
    "syncError": "",
    "syncOkAt": "",
    "exchangeTestnet": False,
}


class DashboardSettingsPatch(BaseModel):
    """Exact payload keys for shifting grid (do not rename)."""

    generatorUpper: float | None = None
    generatorLower: float | None = None
    generatorCount: int | None = None
    initialCapital: float | None = None

    @model_validator(mode="after")
    def _validate(self) -> DashboardSettingsPatch:
        if self.generatorCount is not None and self.generatorCount < 2:
            raise ValueError("generatorCount must be >= 2")
        if self.initialCapital is not None and self.initialCapital <= 0:
            raise ValueError("initialCapital must be > 0")
        return self


@router.get("/{bot_id}/dashboard")
async def get_dashboard(bot_id: str, db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    await futures_account_sync.sync_futures_account_to_hub_once(bot_id)
    row = await db.scalar(select(BotSettings).where(BotSettings.bot_id == bot_id))
    cfg: dict[str, Any] = dict(row.config_json) if row and row.config_json else {}
    live = hub.state
    merged: dict[str, Any] = {**_DEFAULTS, **cfg, **live}
    merged["bot_id"] = bot_id
    k1, k2, paper, _legacy = await get_binance_keys(bot_id)
    merged["credentialsConfigured"] = bool(k1 and k2)
    merged["exchangeTestnet"] = bool(paper) if (k1 and k2) else False
    if k1 and k2:
        merged["binanceApiKeyPreview"] = mask_binance_api_key_preview(k1)
        merged["binanceTestnet"] = bool(paper)
    else:
        merged["binanceApiKeyPreview"] = ""
        merged["binanceTestnet"] = True
    if "activeGridLines" not in merged or merged.get("activeGridLines") in (None, 0):
        merged["activeGridLines"] = int(merged.get("generatorCount") or 5)
    return merged


@router.get("/{bot_id}/klines")
async def get_klines(
    bot_id: str,
    symbol: str = Query(
        ...,
        min_length=1,
        description="USD-M perpetual symbol, e.g. BTCUSDT or DOGEUSDT",
    ),
    interval: str = "5m",
    limit: int = 200,
) -> list[dict[str, float | int]]:
    """
    Public USD-M klines (proxied through BFF for browser CORS).
    Uses **Unified Demo** ``demo-fapi.binance.com`` when paper keys are stored (unless legacy testnet env).
    """
    sym = symbol.strip().upper().replace("/", "")
    if not sym:
        raise HTTPException(status_code=400, detail="symbol must not be empty")

    try:
        k1, k2, paper, legacy = await get_binance_keys(bot_id)
    except Exception as exc:
        _log.exception("klines: get_binance_keys failed")
        raise HTTPException(
            status_code=503,
            detail=f"credentials lookup failed: {type(exc).__name__}: {exc}",
        ) from exc

    use_paper = bool(paper) if (k1 and k2) else False
    iv = interval if interval in _KLINES_INTERVALS else "5m"
    limit_i = max(10, min(int(limit), 500))

    client: BinanceFuturesClient | None = None
    try:
        client = await BinanceFuturesClient.create_for_paper_or_mainnet(
            api_key=k1,
            api_secret=k2,
            paper=use_paper,
            legacy_futures_testnet=legacy,
        )
        raw_rows = await client.futures_klines(symbol=sym, interval=iv, limit=limit_i)
    except BinanceAPIException as exc:
        _log.warning(
            "klines BinanceAPIException code=%s msg=%s",
            getattr(exc, "code", None),
            getattr(exc, "message", exc),
        )
        raise HTTPException(
            status_code=502,
            detail=f"Binance API {getattr(exc, 'code', '')}: {getattr(exc, 'message', str(exc))}",
        ) from exc
    except BinanceRequestException as exc:
        _log.warning("klines BinanceRequestException: %s", exc)
        raise HTTPException(status_code=502, detail=f"Binance request failed: {exc}") from exc
    except Exception as exc:
        _log.exception("klines fetch failed")
        raise HTTPException(
            status_code=502,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    finally:
        if client is not None:
            await client.aclose()

    if not isinstance(raw_rows, list):
        raise HTTPException(status_code=502, detail="bad klines payload")

    out: list[dict[str, float | int]] = []
    for row in raw_rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        o_time = int(row[0]) // 1000
        o, h, low, c = float(row[1]), float(row[2]), float(row[3]), float(row[4])
        out.append({"time": o_time, "open": o, "high": h, "low": low, "close": c})
    return out


@router.patch("/{bot_id}/settings")
async def patch_settings(
    bot_id: str,
    body: DashboardSettingsPatch,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(400, "empty patch")
    row = await db.scalar(select(BotSettings).where(BotSettings.bot_id == bot_id))
    if row is None:
        row = BotSettings(
            bot_id=bot_id,
            strategy_key="alkarrar_pro_shifting_grid",
            config_json={},
            updated_at=datetime.now(timezone.utc),
        )
        db.add(row)
        await db.flush()
    cfg = dict(row.config_json or {})
    cfg.update(patch)
    row.config_json = cfg
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    merged = await hub.merge_state(patch)
    await hub.broadcast({"type": "settings", "data": merged})
    await futures_account_sync.sync_futures_account_to_hub_once(bot_id)
    snap = dict(hub.state)
    snap["bot_id"] = bot_id
    k1, k2, paper, _legacy = await get_binance_keys(bot_id)
    snap["credentialsConfigured"] = bool(k1 and k2)
    snap["exchangeTestnet"] = bool(paper) if (k1 and k2) else False
    return snap


emergency_router = APIRouter()


@emergency_router.post("/emergency_stop")
async def emergency_stop(payload: dict[str, Any] | None = None) -> dict[str, str]:
    """
    Cancel all open futures orders and attempt reduce-only flatten for configured symbol.
    """
    payload = payload or {}
    bot_id = str(payload.get("bot_id", "default"))
    await hub.broadcast(
        {
            "type": "emergency",
            "bot_id": bot_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )
    snap = hub.state
    symbol = str(snap.get("symbol") or "DOGEUSDT").upper().replace("/", "")
    key, secret, paper, legacy = await get_binance_keys(bot_id)
    if not key or not secret:
        _log.warning("emergency_stop: no API keys; broadcast only")
        return {"status": "broadcast_only", "detail": "save keys in dashboard or set BINANCE_API_KEY in .env"}

    client: BinanceFuturesClient | None = None
    try:
        client = await BinanceFuturesClient.create_for_paper_or_mainnet(
            api_key=key,
            api_secret=secret,
            paper=paper,
            legacy_futures_testnet=legacy,
        )
        await client.raw.futures_cancel_all_open_orders(symbol=symbol)
        rows = await client.fetch_positions()
        for p in rows:
            if str(p.get("symbol", "")).upper() != symbol:
                continue
            try:
                qty = float(p.get("positionAmt", 0) or 0.0)
            except (TypeError, ValueError):
                qty = 0.0
            if abs(qty) < 1e-12:
                continue
            side = "SELL" if qty > 0 else "BUY"
            await client.create_order(
                symbol=symbol,
                side=side,
                order_type="MARKET",
                quantity=abs(qty),
                reduce_only=True,
            )
        return {"status": "ok", "symbol": symbol}
    except Exception as exc:
        _log.exception("emergency_stop failed")
        raise HTTPException(502, str(exc)) from exc
    finally:
        if client is not None:
            await client.aclose()
