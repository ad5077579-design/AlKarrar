"""
AlKarrar Pro — streaming-first futures strategy (mark price + user data WebSockets).

Design goals (millisecond-sensitive path):
- No REST ticker polling in the hot path; marks and fills come from WebSockets only.
- Order placement and DB persistence are fire-and-forget (asyncio.create_task + queue worker).
- Kill-switch checks on each mark are O(1) on in-memory state; liquidation price is refreshed
  asynchronously (still not fetch_ticker; uses position information endpoint in a background task).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

from backend.core.binance_client import BinanceFuturesClient
from backend.main_engine import (
    GeneratorBand,
    HedgeBasket,
    HedgeLeg,
    OrderIntent,
    Side,
    build_dca_levels,
    hedge_notionals,
)
from backend.project_paths import data_dir
from backend.strategies.base_strategy import BaseStrategy

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError as _exc:  # pragma: no cover
    raise ImportError(
        "websockets is required for AlKarrarStrategy. Install: pip install websockets"
    ) from _exc

_log = logging.getLogger(__name__)

# Binance USD-M routed endpoints (see Binance "Websocket Market Streams" docs).
_WS_MAINNET = "wss://fstream.binance.com"
_WS_TESTNET = "wss://stream.binancefuture.com"
_WS_UNIFIED_DEMO = "wss://demo-fstream.binance.com"


def _mark_stream_url(base: str, symbols: list[str]) -> str:
    parts = [f"{s.lower().replace('/', '')}@markPrice@1s" for s in symbols]
    if len(parts) == 1:
        return f"{base}/market/ws/{parts[0]}"
    streams = "/".join(parts)
    return f"{base}/market/stream?streams={streams}"


def _user_stream_url(base: str, listen_key: str) -> str:
    return f"{base}/private/ws/{listen_key}"


@dataclass
class KillSwitchConfig:
    max_drawdown_pct: float = 0.15
    """Halt when (peak_equity - equity) / peak_equity exceeds this (e.g. 0.15 = 15%)."""

    liquidation_buffer_pct: float = 0.005
    """Flatten when mark is within this fraction of cached liquidation price (e.g. 0.5%)."""

    min_liquidation_price: float = 0.0
    """Ignore liquidation checks when cached liq price is below this (unknown / spot-like)."""


@dataclass
class RollingSR:
    """Instantaneous support / resistance from rolling min/max of mark (in-memory only)."""

    window: deque[float] = field(default_factory=lambda: deque(maxlen=512))

    def push(self, price: float) -> tuple[float, float]:
        self.window.append(price)
        # O(n) over fixed maxlen (512) — predictable, avoids broken monotonic-queue edge cases.
        lo = min(self.window)
        hi = max(self.window)
        return lo, hi


@dataclass
class CompoundTracker:
    """
    Incremental compounding: avoid recomputing long history each tick.
    equity_multiplier accumulates (1 + chunk_return) from realized PnL chunks.
    """

    base_equity: float = 1.0
    equity_multiplier: float = 1.0

    def apply_realized_chunk(self, delta_usdt: float, reference_notional: float) -> None:
        if reference_notional <= 0:
            return
        r = delta_usdt / reference_notional
        # single multiply — constant time
        self.equity_multiplier *= 1.0 + r
        if not math.isfinite(self.equity_multiplier):
            self.equity_multiplier = 1.0


@dataclass
class MemoryState:
    marks: dict[str, float] = field(default_factory=dict)
    positions_qty: dict[str, float] = field(default_factory=dict)
    wallet_balance_usdt: float = 0.0
    unrealized_usdt: float = 0.0
    liquidation_price: float = 0.0
    peak_equity: float = 0.0
    halted: bool = False
    last_mark_event_ms: int = 0


class AlKarrarStrategy(BaseStrategy):
    """
    Streaming-only strategy: Mark Price stream + User Data stream.
    REST is used only for listenKey lifecycle and async liquidation refresh (not ticker).
    """

    name = "alkarrar"

    def __init__(self, exchange: BinanceFuturesClient | None = None) -> None:
        super().__init__(exchange)
        self._bot_id: str = ""
        self._settings: dict[str, Any] = {}
        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._mem = MemoryState()
        self._sr = RollingSR()
        self._compound = CompoundTracker()
        self._kill_cfg = KillSwitchConfig()
        self._listen_key: str | None = None
        self._symbols: list[str] = []
        self._primary: str = "BTCUSDT"
        self._hedge_symbol: str | None = None
        self._hedge_basket: HedgeBasket | None = None
        self._target_notional_usdt: float = 500.0
        self._hedge_ratio: float = 1.0
        self._generator_count: int = 5
        self._testnet: bool = True
        self._db_path = data_dir() / "trader.db"
        self._db_queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue(maxsize=1)
        self._db_worker_task: asyncio.Task[None] | None = None
        self._liq_refresh_task: asyncio.Task[None] | None = None
        self._listen_keepalive_task: asyncio.Task[None] | None = None
        self._last_grid_sig: tuple[float, float, int] | None = None

    # --- lifecycle ---------------------------------------------------------

    async def on_start(self, bot_id: str, settings: dict[str, Any]) -> None:
        if not isinstance(self._exchange, BinanceFuturesClient):
            raise TypeError("AlKarrarStrategy requires BinanceFuturesClient")
        self._bot_id = bot_id
        self._settings = dict(settings)
        self._running = True
        self._parse_settings(settings)
        await _init_trader_db(self._db_path)
        self._mem = MemoryState()
        self._sr = RollingSR()
        self._compound = CompoundTracker(
            base_equity=float(settings.get("compound_base_equity", 1.0)),
            equity_multiplier=1.0,
        )
        self._kill_cfg = KillSwitchConfig(
            max_drawdown_pct=float(settings.get("max_drawdown_pct", 0.15)),
            liquidation_buffer_pct=float(settings.get("liquidation_buffer_pct", 0.005)),
            min_liquidation_price=float(settings.get("min_liquidation_price", 0.0)),
        )

        raw = self._exchange.raw
        lk = await raw.futures_stream_get_listen_key()
        if isinstance(lk, dict):
            self._listen_key = str(lk.get("listenKey") or lk.get("listen_key") or "")
        else:
            self._listen_key = str(lk)
        if not self._listen_key:
            raise RuntimeError("empty futures listenKey")

        # Same routing contract as ``BinanceFuturesClient`` (REST + WS must match).
        legacy_testnet = bool(getattr(raw, "testnet", False))
        unified_demo = self._exchange.unified_usdm_demo
        if unified_demo:
            base = _WS_UNIFIED_DEMO
            mark_url = _mark_stream_url(base, self._symbols)
            user_url = self._exchange.futures_user_data_stream_url(self._listen_key)
        elif legacy_testnet:
            base = _WS_TESTNET
            mark_url = _mark_stream_url(base, self._symbols)
            user_url = _user_stream_url(base, self._listen_key)
        else:
            base = _WS_MAINNET
            mark_url = _mark_stream_url(base, self._symbols)
            user_url = _user_stream_url(base, self._listen_key)
        _log.info(
            "alkarrar_ws unified_demo=%s legacy_testnet=%s mark=%s user_host=%s",
            unified_demo,
            legacy_testnet,
            mark_url.split("?")[0],
            user_url.split("/")[2] if "://" in user_url else user_url,
        )

        self._db_worker_task = asyncio.create_task(self._db_worker_loop(), name="trader-db-worker")
        self._listen_keepalive_task = asyncio.create_task(
            self._listen_key_keepalive_loop(), name="listen-keepalive"
        )
        self._liq_refresh_task = asyncio.create_task(self._liquidation_refresh_loop(), name="liq-refresh")
        self._tasks.append(asyncio.create_task(self._ws_mark_loop(mark_url), name="mark-ws"))
        self._tasks.append(asyncio.create_task(self._ws_user_loop(user_url), name="user-ws"))

    async def on_stop(self, bot_id: str) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        if self._listen_keepalive_task:
            self._listen_keepalive_task.cancel()
            self._listen_keepalive_task = None
        if self._liq_refresh_task:
            self._liq_refresh_task.cancel()
            self._liq_refresh_task = None
        if self._db_worker_task:
            try:
                self._db_queue.put_nowait(None)
            except asyncio.QueueFull:
                try:
                    _ = self._db_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self._db_queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._db_worker_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                self._db_worker_task.cancel()
            self._db_worker_task = None
        if self._listen_key and isinstance(self._exchange, BinanceFuturesClient):
            try:
                await self._exchange.raw.futures_stream_close(listenKey=self._listen_key)
            except Exception:
                _log.exception("listenKey close failed")
        self._listen_key = None

    async def on_tick(self, bot_id: str, market: dict[str, Any]) -> None:
        """
        Streaming mode: decisions run on WebSocket handlers.
        ``market`` polling / REST ticks must not drive this strategy.
        """
        return

    # --- settings ----------------------------------------------------------

    def _parse_settings(self, settings: dict[str, Any]) -> None:
        self._testnet = bool(settings.get("testnet", True))
        self._primary = str(settings.get("symbol", "BTCUSDT")).upper().replace("/", "")
        self._target_notional_usdt = float(settings.get("target_notional_usdt", 500.0))
        self._hedge_ratio = float(settings.get("hedge_ratio", 1.0))
        self._generator_count = int(settings.get("generator_count", 5))
        hedge_sym = settings.get("hedge_symbol")
        self._hedge_symbol = (
            str(hedge_sym).upper().replace("/", "") if hedge_sym else None
        )
        self._symbols = [self._primary]
        if self._hedge_symbol and self._hedge_symbol not in self._symbols:
            self._symbols.append(self._hedge_symbol)

        if settings.get("hedge_basket"):
            self._hedge_basket = HedgeBasket.model_validate(settings["hedge_basket"])
        else:
            self._hedge_basket = HedgeBasket(
                primary=HedgeLeg(symbol=self._primary, side="long", target_notional_usdt=self._target_notional_usdt),
                hedge=HedgeLeg(
                    symbol=self._hedge_symbol or self._primary,
                    side="short",
                    target_notional_usdt=self._target_notional_usdt * 0.5,
                ),
                hedge_ratio=self._hedge_ratio,
            )

    # --- WebSocket loops ----------------------------------------------------

    async def _ws_mark_loop(self, url: str) -> None:
        while self._running:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=60,
                    max_queue=32,
                ) as ws:
                    while self._running:
                        raw = await ws.recv()
                        self._handle_mark_message_raw(raw)
            except (ConnectionClosed, OSError, asyncio.CancelledError) as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                _log.warning("mark ws dropped: %s", exc)
                await asyncio.sleep(0.5)
            except Exception:
                _log.exception("mark ws error")
                await asyncio.sleep(1.0)

    async def _ws_user_loop(self, url: str) -> None:
        while self._running:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=60,
                    max_queue=256,
                ) as ws:
                    while self._running:
                        raw = await ws.recv()
                        self._handle_user_message_raw(raw)
            except (ConnectionClosed, OSError, asyncio.CancelledError) as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                _log.warning("user ws dropped: %s", exc)
                await asyncio.sleep(0.5)
            except Exception:
                _log.exception("user ws error")
                await asyncio.sleep(1.0)

    # --- hot path (parse + memory + schedule) ------------------------------

    def _handle_mark_message_raw(self, raw: str | bytes) -> None:
        t0 = time.perf_counter()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(msg, dict) and "data" in msg:
            msg = msg["data"]
        if not isinstance(msg, dict):
            return
        if msg.get("e") != "markPriceUpdate":
            return
        sym = str(msg.get("s", "")).upper()
        try:
            price = float(msg.get("p", "0") or 0.0)
        except (TypeError, ValueError):
            return
        if sym and price > 0:
            self._mem.marks[sym] = price
        self._mem.last_mark_event_ms = int(msg.get("E", 0) or 0)
        primary_mark = self._mem.marks.get(self._primary, price)
        support, resistance = self._sr.push(primary_mark)
        if self._mem.halted:
            return
        if self._kill_switch_triggered(primary_mark):
            self._mem.halted = True
            asyncio.create_task(self._emergency_flatten_all(), name="kill-flatten")
            asyncio.create_task(
                self._enqueue_db_snapshot(reason="kill_switch"),
                name="kill-db",
            )
            return
        asyncio.create_task(
            self._advanced_grid_tick(primary_mark, support, resistance),
            name="grid",
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if elapsed_ms > 10.0:
            _log.debug("mark handler slow_ms=%.3f", elapsed_ms)

    def _handle_user_message_raw(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict):
            return
        et = msg.get("e")
        if et == "ACCOUNT_UPDATE":
            self._apply_account_update(msg)
        elif et == "ORDER_TRADE_UPDATE":
            self._apply_order_trade_update(msg)
        asyncio.create_task(self._enqueue_db_snapshot(reason=str(et)), name="user-db")

    def _apply_account_update(self, msg: dict[str, Any]) -> None:
        a = msg.get("a") or {}
        balances = a.get("B") or []
        for b in balances:
            if not isinstance(b, dict):
                continue
            if str(b.get("a", "")).upper() == "USDT":
                try:
                    self._mem.wallet_balance_usdt = float(b.get("wb", 0) or 0.0)
                except (TypeError, ValueError):
                    pass
        unreal = 0.0
        positions = a.get("P") or []
        for p in positions:
            if not isinstance(p, dict):
                continue
            sym = str(p.get("s", "")).upper()
            try:
                qty = float(p.get("pa", 0) or 0.0)
            except (TypeError, ValueError):
                qty = 0.0
            self._mem.positions_qty[sym] = qty
            try:
                unreal += float(p.get("up", 0) or 0.0)
            except (TypeError, ValueError):
                pass
        self._mem.unrealized_usdt = unreal
        eq = self._mem.wallet_balance_usdt + self._mem.unrealized_usdt
        self._mem.peak_equity = max(self._mem.peak_equity, eq)

    def _apply_order_trade_update(self, msg: dict[str, Any]) -> None:
        o = msg.get("o") or {}
        if not isinstance(o, dict):
            return
        status = str(o.get("X", ""))
        if status != "FILLED":
            return
        try:
            realized = float(o.get("rp", 0) or 0.0)
        except (TypeError, ValueError):
            realized = 0.0
        if realized == 0.0:
            return
        ref = max(self._target_notional_usdt, 1.0)
        self._compound.apply_realized_chunk(realized, ref)

    # --- kill switch -------------------------------------------------------

    def _kill_switch_triggered(self, mark: float) -> bool:
        eq = self._mem.wallet_balance_usdt + self._mem.unrealized_usdt
        self._mem.peak_equity = max(self._mem.peak_equity, eq)
        if self._mem.peak_equity > 0:
            dd = (self._mem.peak_equity - eq) / self._mem.peak_equity
            if dd > self._kill_cfg.max_drawdown_pct:
                _log.error("kill: max_drawdown dd=%.4f cap=%.4f", dd, self._kill_cfg.max_drawdown_pct)
                return True
        liq = self._mem.liquidation_price
        if liq > self._kill_cfg.min_liquidation_price and mark > 0:
            buf = self._kill_cfg.liquidation_buffer_pct
            qty = self._mem.positions_qty.get(self._primary, 0.0)
            if qty > 0 and mark <= liq * (1.0 + buf):
                _log.error("kill: long mark near liquidation mark=%s liq=%s", mark, liq)
                return True
            if qty < 0 and mark >= liq * (1.0 - buf):
                _log.error("kill: short mark near liquidation mark=%s liq=%s", mark, liq)
                return True
        return False

    # --- grid + hedge (async tasks, no await in mark handler) --------------

    async def _advanced_grid_tick(self, mark: float, support: float, resistance: float) -> None:
        if self._mem.halted:
            return
        basket = self._live_hedge_basket()
        p_sym = basket.primary.symbol.upper().replace("/", "")
        h_sym = basket.hedge.symbol.upper().replace("/", "")
        p_px = self._mem.marks.get(p_sym, mark)
        h_px = self._mem.marks.get(h_sym, p_px)
        marks_map = {p_sym: p_px, h_sym: h_px}
        try:
            p_not, h_not = hedge_notionals(basket)
        except Exception:
            _log.exception("hedge_notionals failed")
            return
        lo = min(support, resistance)
        hi = max(support, resistance)
        if hi <= lo:
            hi = lo * 1.001
        sig = (round(lo, 6), round(hi, 6), self._generator_count)
        if self._last_grid_sig == sig:
            return
        self._last_grid_sig = sig
        band = GeneratorBand(generatorUpper=hi, generatorLower=lo, generatorCount=self._generator_count)
        try:
            levels = build_dca_levels(band, mode="equal")
        except Exception:
            return
        slice_p = (p_not / max(p_px, 1e-12)) / max(len(levels), 1)
        slice_h = (h_not / max(h_px, 1e-12)) / max(len(levels), 1)
        for i, price in enumerate(levels):
            side_p = Side.BUY if i % 2 == 0 else Side.SELL
            side_h = Side.SELL if side_p == Side.BUY else Side.BUY
            p_intent = OrderIntent(
                symbol=p_sym,
                side=side_p,
                amount_base=slice_p,
                price=price,
                tag=f"grid-p-{i}",
            )
            h_intent = OrderIntent(
                symbol=h_sym,
                side=side_h,
                amount_base=slice_h,
                price=price,
                tag=f"grid-h-{i}",
            )
            asyncio.create_task(self._place_limit_intent(p_intent, marks_map), name=f"ord-p-{i}")
            if h_sym != p_sym:
                asyncio.create_task(self._place_limit_intent(h_intent, marks_map), name=f"ord-h-{i}")

    def _live_hedge_basket(self) -> HedgeBasket:
        assert self._hedge_basket is not None
        hb = self._hedge_basket
        return HedgeBasket(
            primary=HedgeLeg(
                symbol=hb.primary.symbol,
                side=hb.primary.side,
                target_notional_usdt=self._target_notional_usdt,
            ),
            hedge=HedgeLeg(
                symbol=hb.hedge.symbol,
                side=hb.hedge.side,
                target_notional_usdt=hb.hedge.target_notional_usdt,
            ),
            hedge_ratio=self._hedge_ratio,
        )

    async def _place_limit_intent(self, intent: OrderIntent, marks: dict[str, float]) -> None:
        if not isinstance(self._exchange, BinanceFuturesClient) or self._mem.halted:
            return
        px = marks.get(intent.symbol.upper().replace("/", ""), intent.price or 0.0)
        if not intent.price or px <= 0:
            return
        try:
            await self._exchange.create_order(
                symbol=intent.symbol.upper().replace("/", ""),
                side=intent.side.value,
                order_type="LIMIT",
                quantity=float(intent.amount_base),
                price=float(intent.price),
                time_in_force="GTC",
                reduce_only=intent.reduce_only,
            )
        except Exception:
            _log.exception("order failed tag=%s", intent.tag)

    async def _emergency_flatten_all(self) -> None:
        if not isinstance(self._exchange, BinanceFuturesClient):
            return
        for sym, qty in list(self._mem.positions_qty.items()):
            if abs(qty) < 1e-12:
                continue
            side = "SELL" if qty > 0 else "BUY"
            asyncio.create_task(
                self._market_reduce_only(sym, abs(qty), side),
                name=f"flatten-{sym}",
            )

    async def _market_reduce_only(self, sym: str, quantity: float, side: str) -> None:
        if not isinstance(self._exchange, BinanceFuturesClient):
            return
        try:
            await self._exchange.create_order(
                symbol=sym,
                side=side,
                order_type="MARKET",
                quantity=quantity,
                reduce_only=True,
            )
        except Exception:
            _log.exception("emergency flatten failed sym=%s", sym)

    # --- background REST (not ticker; not on mark hot path) --------------

    async def _liquidation_refresh_loop(self) -> None:
        while self._running:
            try:
                if isinstance(self._exchange, BinanceFuturesClient):
                    rows = await self._exchange.fetch_positions()
                    for row in rows:
                        if str(row.get("symbol", "")).upper() != self._primary:
                            continue
                        lp = row.get("liquidationPrice") or row.get("liquidation_price")
                        if lp is not None:
                            try:
                                self._mem.liquidation_price = float(lp)
                            except (TypeError, ValueError):
                                pass
                            break
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.debug("liq refresh failed", exc_info=True)
            await asyncio.sleep(45.0)

    async def _listen_key_keepalive_loop(self) -> None:
        while self._running and self._listen_key:
            try:
                if self._listen_key and isinstance(self._exchange, BinanceFuturesClient):
                    await self._exchange.raw.futures_stream_keepalive(listenKey=self._listen_key)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.warning("listenKey keepalive failed", exc_info=True)
            await asyncio.sleep(30 * 60)

    # --- trader.db (single-slot queue, background worker) -------------------

    def _enqueue_db_snapshot_sync(self, reason: str) -> None:
        payload = {
            "reason": reason,
            "ts_ms": int(time.time() * 1000),
            "marks": dict(self._mem.marks),
            "positions_qty": dict(self._mem.positions_qty),
            "wallet_usdt": self._mem.wallet_balance_usdt,
            "unrealized_usdt": self._mem.unrealized_usdt,
            "peak_equity": self._mem.peak_equity,
            "liquidation_price": self._mem.liquidation_price,
            "compound_multiplier": self._compound.equity_multiplier,
            "halted": self._mem.halted,
        }
        blob = json.dumps(payload, separators=(",", ":"))
        try:
            self._db_queue.put_nowait((self._bot_id, blob))
        except asyncio.QueueFull:
            try:
                self._db_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._db_queue.put_nowait((self._bot_id, blob))
            except asyncio.QueueFull:
                pass

    async def _enqueue_db_snapshot(self, reason: str) -> None:
        """Async wrapper so call sites can ``create_task`` without blocking."""
        self._enqueue_db_snapshot_sync(reason)

    async def _db_worker_loop(self) -> None:
        while True:
            item = await self._db_queue.get()
            if item is None:
                break
            bot_id, blob = item
            try:
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute(
                        """
                        INSERT INTO trade_snapshots (bot_id, payload, updated_ms)
                        VALUES (?, ?, ?)
                        ON CONFLICT(bot_id) DO UPDATE SET
                          payload = excluded.payload,
                          updated_ms = excluded.updated_ms
                        """,
                        (bot_id, blob, int(time.time() * 1000)),
                    )
                    await db.commit()
            except Exception:
                _log.exception("trader.db write failed")


async def _init_trader_db(path: Path) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_snapshots (
              bot_id TEXT PRIMARY KEY,
              payload TEXT NOT NULL,
              updated_ms INTEGER NOT NULL
            )
            """
        )
        await db.commit()
