"""Dashboard BFF: snapshot, contractual settings patch, emergency stop."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from binance.exceptions import BinanceAPIException, BinanceRequestException
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api import spot_account_sync
from backend.api.audit_log_service import list_audit_logs
from backend.api.bot_hub import hub
from backend.api.dashboard_meta import apply_credentials_meta
from backend.api.credential_resolver import exchange_testnet_flag, get_binance_keys, mask_binance_api_key_preview
from backend.main_engine import EngineSettings
from backend.api.dependencies import get_db_session
from backend.api.grid_manager import grid_manager
from backend.api.grid_runner import calibrated_doge_grid_settings
from backend.api.trade_journal import (
    list_trades_from_db,
    summarize_trades,
    sync_trades_from_exchange,
)
from backend.core.binance_client import BinanceSpotClient
from backend.core.exchange_filters import fetch_symbol_filters, normalize_order, quantize_price
from backend.core.spot_market_filters import (
    is_grid_tradable_base,
    is_grid_tradable_symbol,
    list_excluded_stable_usdt_pairs,
)
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
    "maxGeneratorCount": 9999,
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


class GridStopBody(BaseModel):
    """Stop one grid by symbol, or all grids if symbol is omitted."""

    symbol: str | None = None


class GridStartBody(BaseModel):
    """Start live shifting grid (defaults: tight DOGE band from current mark)."""

    calibrate: bool = True
    symbol: str | None = None
    generatorUpper: float | None = None
    generatorLower: float | None = None
    generatorCount: int | None = None
    maxGeneratorCount: int | None = None
    initialCapital: float | None = None
    levels: int = 8
    trailingOffset: float | None = None
    trailing_stop_pct: float | None = None
    compoundingFactor: float | None = None
    profit_injection_mode: str | None = None
    max_slippage_pct: float | None = None
    dca_mode: str | None = None


class DashboardSettingsPatch(BaseModel):
    """Exact payload keys for shifting grid (do not rename)."""

    symbol: str | None = None
    generatorUpper: float | None = None
    generatorLower: float | None = None
    generatorCount: int | None = None
    maxGeneratorCount: int | None = None
    initialCapital: float | None = None
    trailingOffset: float | None = None
    trailing_stop_pct: float | None = None
    compoundingFactor: float | None = None
    profit_injection_mode: str | None = None
    max_slippage_pct: float | None = None
    dca_mode: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> DashboardSettingsPatch:
        if self.symbol is not None:
            sym = self.symbol.strip().upper().replace("/", "")
            if len(sym) < 4:
                raise ValueError("symbol must be at least 4 characters")
            self.symbol = sym
        if self.generatorCount is not None and self.generatorCount < 2:
            raise ValueError("generatorCount must be >= 2")
        if self.maxGeneratorCount is not None and self.maxGeneratorCount < 2:
            raise ValueError("maxGeneratorCount must be >= 2")
        if (
            self.generatorCount is not None
            and self.maxGeneratorCount is not None
            and self.maxGeneratorCount <= self.generatorCount
        ):
            raise ValueError("maxGeneratorCount must be greater than generatorCount for hybrid expansion")
        if self.initialCapital is not None and self.initialCapital <= 0:
            raise ValueError("initialCapital must be > 0")
        if self.profit_injection_mode is not None:
            m = self.profit_injection_mode.strip().lower()
            if m not in ("expand_count", "compound_size"):
                raise ValueError("profit_injection_mode must be expand_count or compound_size")
            self.profit_injection_mode = m
        if self.dca_mode is not None:
            d = self.dca_mode.strip().lower()
            if d not in ("equal", "log"):
                raise ValueError("dca_mode must be equal or log")
            self.dca_mode = d
        return self


def _merge_advanced_grid_settings(
    settings: dict[str, Any],
    *,
    hub_state: dict[str, Any],
    body: GridStartBody | DashboardSettingsPatch | None = None,
) -> None:
    """Overlay advanced strategy keys from request body then hub config."""
    body_d = body.model_dump(exclude_unset=True) if body is not None else {}
    upper = float(settings.get("generatorUpper") or hub_state.get("generatorUpper") or 1)

    def pick(key: str, default: Any) -> Any:
        if key in body_d and body_d[key] is not None:
            return body_d[key]
        if key in settings and settings[key] is not None:
            return settings[key]
        if key in hub_state and hub_state[key] is not None:
            return hub_state[key]
        return default

    settings["trailingOffset"] = float(pick("trailingOffset", max(upper * 0.002, 1e-6)))
    settings["compoundingFactor"] = float(pick("compoundingFactor", 0.05))
    settings["trailing_stop_pct"] = float(pick("trailing_stop_pct", 0.01))
    mode = str(pick("profit_injection_mode", "expand_count")).lower()
    settings["profit_injection_mode"] = "compound_size" if mode == "compound_size" else "expand_count"
    dca = str(pick("dca_mode", "equal")).lower()
    settings["dca_mode"] = "log" if dca == "log" else "equal"
    slip = pick("max_slippage_pct", None)
    if slip is not None:
        settings["max_slippage_pct"] = max(0.0, float(slip))


@router.get("/{bot_id}/markets")
async def get_markets(
    bot_id: str,
    quote: str = Query("USDT", min_length=3, max_length=12),
) -> dict[str, Any]:
    """
    Live Spot symbols from Binance ``exchangeInfo`` + ``ticker/24hr``.
    Uses the same demo/testnet/mainnet host as stored credentials (or ``.env``).
    """
    try:
        k1, k2, env, legacy = await get_binance_keys(bot_id)
    except Exception as exc:
        _log.exception("markets: get_binance_keys failed")
        raise HTTPException(
            status_code=503,
            detail=f"credentials lookup failed: {type(exc).__name__}: {exc}",
        ) from exc

    stream_env = env if (k1 and k2) else EngineSettings().resolved_binance_env()
    quote_u = quote.strip().upper()

    client: BinanceSpotClient | None = None
    try:
        client = await BinanceSpotClient.create_for_env(
            api_key=k1,
            api_secret=k2,
            env=stream_env,
        )
        info = await client.get_exchange_info()
        tickers = await client.get_tickers_24hr()
    except BinanceAPIException as exc:
        _log.warning(
            "markets BinanceAPIException code=%s msg=%s",
            getattr(exc, "code", None),
            getattr(exc, "message", exc),
        )
        raise HTTPException(
            status_code=502,
            detail=f"Binance API {getattr(exc, 'code', '')}: {getattr(exc, 'message', str(exc))}",
        ) from exc
    except BinanceRequestException as exc:
        _log.warning("markets BinanceRequestException: %s", exc)
        raise HTTPException(status_code=502, detail=f"Binance request failed: {exc}") from exc
    except Exception as exc:
        _log.exception("markets fetch failed")
        raise HTTPException(
            status_code=502,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    finally:
        if client is not None:
            await client.aclose()

    ticker_by_sym: dict[str, dict[str, Any]] = {}
    for row in tickers:
        sym = str(row.get("symbol", "")).upper()
        if sym:
            ticker_by_sym[sym] = row

    symbols_out: list[dict[str, Any]] = []
    excluded_stable: list[str] = []
    raw_symbols = info.get("symbols") if isinstance(info, dict) else None
    if isinstance(raw_symbols, list):
        if quote_u == "USDT":
            excluded_stable = list_excluded_stable_usdt_pairs(
                [r for r in raw_symbols if isinstance(r, dict)]
            )
        for row in raw_symbols:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol", "")).upper()
            if not sym.endswith(quote_u):
                continue
            if str(row.get("status", "")).upper() != "TRADING":
                continue
            base = str(row.get("baseAsset") or sym[: -len(quote_u)] or sym)
            if quote_u == "USDT" and not is_grid_tradable_base(base):
                continue
            tick = ticker_by_sym.get(sym, {})
            try:
                last_price = float(tick.get("lastPrice") or tick.get("weightedAvgPrice") or 0)
            except (TypeError, ValueError):
                last_price = 0.0
            try:
                pct = float(tick.get("priceChangePercent") or 0)
            except (TypeError, ValueError):
                pct = 0.0
            try:
                qvol = float(tick.get("quoteVolume") or 0)
            except (TypeError, ValueError):
                qvol = 0.0
            symbols_out.append(
                {
                    "symbol": sym,
                    "baseAsset": base,
                    "lastPrice": last_price,
                    "priceChangePercent": pct,
                    "quoteVolume": qvol,
                }
            )

    symbols_out.sort(key=lambda x: float(x.get("quoteVolume") or 0), reverse=True)

    return {
        "quote": quote_u,
        "exchangeTestnet": exchange_testnet_flag(stream_env),
        "binanceEnv": stream_env,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols_out,
        "excludedStableSymbols": excluded_stable,
    }


@router.get("/{bot_id}/trades")
async def get_trades(
    bot_id: str,
    symbol: str | None = Query(default=None, description="Spot symbol; defaults to bot hub symbol"),
    limit: int = Query(default=100, ge=1, le=500),
    sync: bool = Query(default=True, description="Refresh from Binance userTrades when API keys exist"),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Trade journal: Binance fills (REST sync) + SQLite cache; live fills via user stream WS."""
    sym = (symbol or str(hub.state.get("symbol") or "DOGEUSDT")).strip().upper().replace("/", "")
    if not sym:
        raise HTTPException(status_code=400, detail="symbol must not be empty")
    limit_i = max(10, min(int(limit), 500))

    try:
        k1, k2, env, legacy = await get_binance_keys(bot_id)
    except Exception as exc:
        _log.exception("trades: get_binance_keys failed")
        raise HTTPException(
            status_code=503,
            detail=f"credentials lookup failed: {type(exc).__name__}: {exc}",
        ) from exc

    source = "database"
    trades: list[dict[str, Any]] = []
    sync_error: str | None = None

    if sync and k1 and k2:
        client: BinanceSpotClient | None = None
        try:
            client = await BinanceSpotClient.create_for_env(
                api_key=k1,
                api_secret=k2,
                env=env,
            )
            trades = await sync_trades_from_exchange(
                client, db, bot_id=bot_id, symbol=sym, limit=limit_i
            )
            source = "binance"
        except BinanceAPIException as exc:
            sync_error = f"Binance API {getattr(exc, 'code', '')}: {getattr(exc, 'message', str(exc))}"
            _log.warning("trades sync BinanceAPIException: %s", sync_error)
        except BinanceRequestException as exc:
            sync_error = f"Binance request failed: {exc}"
            _log.warning("trades sync BinanceRequestException: %s", exc)
        except Exception as exc:
            sync_error = f"{type(exc).__name__}: {exc}"
            _log.exception("trades sync failed")
        finally:
            if client is not None:
                await client.aclose()

    if not trades:
        trades = await list_trades_from_db(db, bot_id=bot_id, symbol=sym, limit=limit_i)

    return {
        "bot_id": bot_id,
        "symbol": sym,
        "source": source,
        "syncError": sync_error,
        "exchangeTestnet": exchange_testnet_flag(env) if (k1 and k2) else False,
        "binanceEnv": env if (k1 and k2) else "",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": summarize_trades(trades),
        "trades": trades,
    }


async def _persist_bot_config(db: AsyncSession, bot_id: str, patch: dict[str, Any]) -> None:
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


@router.get("/{bot_id}/grid/status")
async def grid_status(bot_id: str) -> dict[str, Any]:
    grids = grid_manager.status_dict()
    active = sorted(grids.keys())
    return {"bot_id": bot_id, "activeSymbols": active, "count": len(active), "grids": grids}


@router.get("/{bot_id}/audit")
async def get_audit_logs(
    bot_id: str,
    symbol: str | None = Query(None, description="Filter by Spot symbol, e.g. DOGEUSDT"),
    limit: int = Query(200, ge=1, le=500),
) -> dict[str, Any]:
    """Structured engine audit trail (decisions as rows)."""
    logs = await list_audit_logs(bot_id=bot_id, symbol=symbol, limit=limit)
    return {"bot_id": bot_id, "symbol": symbol, "limit": limit, "count": len(logs), "logs": logs}


@router.post("/{bot_id}/grid/start")
async def grid_start(
    bot_id: str,
    body: GridStartBody | None = None,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    body = body or GridStartBody()
    sym = str(body.symbol or hub.state.get("symbol") or "DOGEUSDT").upper().replace("/", "")
    if not is_grid_tradable_symbol(sym):
        raise HTTPException(
            status_code=400,
            detail=f"{sym}: عملة مستقرة/نقدية — غير مناسبة لشبكة التداول (اختر عملة متقلّبة مثل DOGE أو BTC)",
        )

    settings: dict[str, Any]
    if body.calibrate and body.generatorUpper is None and body.generatorLower is None:
        k1, k2, env, legacy = await get_binance_keys(bot_id)
        if not k1 or not k2:
            raise HTTPException(status_code=400, detail="Binance keys required to calibrate grid")
        client: BinanceSpotClient | None = None
        try:
            client = await BinanceSpotClient.create_for_env(
                api_key=k1,
                api_secret=k2,
                env=env,
            )
            t = await client.fetch_ticker(sym)
            mark = float(t.get("price") or t.get("lastPrice") or 0)
        finally:
            if client is not None:
                await client.aclose()
        if mark <= 0:
            raise HTTPException(status_code=502, detail="could not fetch mark price for calibration")
        settings = calibrated_doge_grid_settings(
            mark,
            levels=body.levels,
            capital_usdt=float(body.initialCapital or 40.0),
        )
        settings["symbol"] = sym
        if body.generatorCount is not None:
            settings["generatorCount"] = max(2, min(int(body.generatorCount), 64))
            settings["maxGeneratorCount"] = max(
                settings["maxGeneratorCount"],
                settings["generatorCount"],
            )
        if body.maxGeneratorCount is not None:
            settings["maxGeneratorCount"] = max(int(body.maxGeneratorCount), int(settings["generatorCount"]))
        _merge_advanced_grid_settings(settings, hub_state=dict(hub.state), body=body)
    else:
        gcount = int(body.generatorCount or hub.state.get("generatorCount") or 8)
        mg = body.maxGeneratorCount if body.maxGeneratorCount is not None else hub.state.get("maxGeneratorCount")
        if mg is None:
            mg = max(8, gcount)
        settings = {
            "symbol": sym,
            "generatorUpper": float(body.generatorUpper or hub.state.get("generatorUpper") or 0),
            "generatorLower": float(body.generatorLower or hub.state.get("generatorLower") or 0),
            "generatorCount": gcount,
            "maxGeneratorCount": max(int(mg), gcount),
            "initialCapital": float(body.initialCapital or hub.state.get("initialCapital") or 40),
        }

    _merge_advanced_grid_settings(settings, hub_state=dict(hub.state), body=body)

    gc_i = int(settings["generatorCount"])
    mg_i = int(settings.get("maxGeneratorCount") or (gc_i + 1))
    if mg_i <= gc_i:
        mg_i = gc_i + 1
    settings["maxGeneratorCount"] = mg_i

    patch = {
        k: settings[k]
        for k in (
            "symbol",
            "generatorUpper",
            "generatorLower",
            "generatorCount",
            "maxGeneratorCount",
            "initialCapital",
            "trailingOffset",
            "trailing_stop_pct",
            "compoundingFactor",
            "profit_injection_mode",
            "max_slippage_pct",
            "dca_mode",
        )
        if k in settings
    }
    await _persist_bot_config(db, bot_id, patch)
    await hub.merge_state(patch)

    try:
        result = await grid_manager.start(bot_id, settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _log.exception("grid_start failed")
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}") from exc

    await spot_account_sync.sync_spot_account_to_hub_once(bot_id)
    return result


@router.post("/{bot_id}/grid/stop")
async def grid_stop(bot_id: str, body: GridStopBody | None = None) -> dict[str, Any]:
    body = body or GridStopBody()
    sym = (
        body.symbol.strip().upper().replace("/", "")
        if isinstance(body.symbol, str) and body.symbol.strip()
        else None
    )
    if sym:
        st = await grid_manager.stop(sym)
        await spot_account_sync.sync_spot_account_to_hub_once(bot_id)
        return {"bot_id": bot_id, **st}
    st = await grid_manager.stop(None)
    await spot_account_sync.sync_spot_account_to_hub_once(bot_id)
    return {"bot_id": bot_id, **st}


@router.get("/{bot_id}/dashboard")
async def get_dashboard(bot_id: str, db: AsyncSession = Depends(get_db_session)) -> dict[str, Any]:
    asyncio.create_task(spot_account_sync.sync_spot_account_to_hub_once(bot_id))
    row = await db.scalar(select(BotSettings).where(BotSettings.bot_id == bot_id))
    cfg: dict[str, Any] = dict(row.config_json) if row and row.config_json else {}
    live = hub.state
    merged: dict[str, Any] = {**_DEFAULTS, **cfg, **live}
    merged["bot_id"] = bot_id
    await apply_credentials_meta(bot_id, merged)
    if "activeGridLines" not in merged or merged.get("activeGridLines") in (None, 0):
        merged["activeGridLines"] = int(merged.get("generatorCount") or 5)
    return merged


@router.get("/{bot_id}/klines")
async def get_klines(
    bot_id: str,
    symbol: str = Query(
        ...,
        min_length=1,
        description="Spot symbol, e.g. BTCUSDT or DOGEUSDT",
    ),
    interval: str = "5m",
    limit: int = 200,
) -> list[dict[str, float | int]]:
    """Public Spot klines (proxied through BFF for browser CORS). Paper keys → testnet.binance.vision."""
    sym = symbol.strip().upper().replace("/", "")
    if not sym:
        raise HTTPException(status_code=400, detail="symbol must not be empty")

    try:
        k1, k2, env, legacy = await get_binance_keys(bot_id)
    except Exception as exc:
        _log.exception("klines: get_binance_keys failed")
        raise HTTPException(
            status_code=503,
            detail=f"credentials lookup failed: {type(exc).__name__}: {exc}",
        ) from exc

    stream_env = env if (k1 and k2) else EngineSettings().resolved_binance_env()
    iv = interval if interval in _KLINES_INTERVALS else "5m"
    limit_i = max(10, min(int(limit), 500))

    client: BinanceSpotClient | None = None
    try:
        client = await BinanceSpotClient.create_for_env(
            api_key=k1,
            api_secret=k2,
            env=stream_env,
        )
        raw_rows = await client.get_klines(symbol=sym, interval=iv, limit=limit_i)
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
    await apply_credentials_meta(bot_id, merged)
    sym = merged.get("symbol")
    if isinstance(sym, str) and sym.strip():
        await hub.broadcast_room(sym, {"type": "settings", "data": merged})
    else:
        await hub.broadcast({"type": "settings", "data": merged})
    asyncio.create_task(spot_account_sync.sync_spot_account_to_hub_once(bot_id))
    snap = dict(merged)
    snap["bot_id"] = bot_id
    return snap


emergency_router = APIRouter()


@emergency_router.post("/emergency_stop")
async def emergency_stop(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Cancel all open Spot orders and market-sell remaining base asset for configured symbol.
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
    symbols = grid_manager.active_symbols()
    if not symbols:
        fb = str(hub.state.get("symbol") or "DOGEUSDT").upper().replace("/", "")
        symbols = [fb]
    key, secret, env, legacy = await get_binance_keys(bot_id)
    if not key or not secret:
        _log.warning("emergency_stop: no API keys; broadcast only")
        return {"status": "broadcast_only", "detail": "save keys in dashboard or set BINANCE_API_KEY in .env"}

    client: BinanceSpotClient | None = None
    try:
        client = await BinanceSpotClient.create_for_env(
            api_key=key,
            api_secret=secret,
            env=env,
        )
        for symbol in symbols:
            await client.cancel_all_open_orders(symbol=symbol)
            acc = await client.fetch_account()
            free_base = client.base_asset_free(acc, symbol)
            if free_base > 0:
                filters = await fetch_symbol_filters(client, symbol)
                mark_tick = await client.fetch_ticker(symbol)
                mark = float(mark_tick.get("price") or 0)
                _, qty_s = normalize_order(mark, free_base, filters)
                if float(qty_s) > 0:
                    await client.create_order(
                        symbol=symbol,
                        side="SELL",
                        order_type="MARKET",
                        quantity=qty_s,
                    )
        return {"status": "ok", "symbols": symbols}
    except Exception as exc:
        _log.exception("emergency_stop failed")
        raise HTTPException(502, str(exc)) from exc
    finally:
        if client is not None:
            await client.aclose()
