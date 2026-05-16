"""In-memory bot runtime + WebSocket fan-out (BFF). Per-symbol rooms for multi-grid."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

from backend.api.dashboard_meta import apply_credentials_meta

_DEFAULT_STATE: dict[str, Any] = {
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
    "orders": [],
    "syncError": "",
    "syncOkAt": "",
    "exchangeTestnet": False,
}

_ACCOUNT_KEYS = frozenset(
    {
        "totalWalletBalance",
        "totalMarginBalance",
        "currentCapital",
        "marginBalance",
        "availableBalance",
        "floatingPnl",
        "realizedPnl",
        "syncError",
        "syncOkAt",
        "exchangeTestnet",
        "binanceEnv",
    }
)


def _normalize_sym(symbol: str) -> str:
    return symbol.strip().upper().replace("/", "")


class BotHub:
    """Single-process hub; extend with Redis for horizontal scaling."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._account: dict[str, Any] = {
            "totalWalletBalance": 0.0,
            "totalMarginBalance": 0.0,
            "currentCapital": 0.0,
            "marginBalance": 0.0,
            "availableBalance": 0.0,
            "floatingPnl": 0.0,
            "realizedPnl": 0.0,
            "syncError": "",
            "syncOkAt": "",
            "exchangeTestnet": False,
            "binanceEnv": "",
        }
        self._rooms: dict[str, dict[str, Any]] = {}
        self._clients: list[WebSocket] = []
        self._last_focus_symbol = "DOGEUSDT"

    def _room_defaults(self, symbol: str) -> dict[str, Any]:
        d = dict(_DEFAULT_STATE)
        d["symbol"] = _normalize_sym(symbol)
        return d

    @property
    def state(self) -> dict[str, Any]:
        """Flat view: account metrics + focused symbol room (backward compat REST/WS)."""
        return dict(self.flat_state(self._last_focus_symbol))

    @property
    def last_focus_symbol(self) -> str:
        return self._last_focus_symbol

    def flat_state(self, focus_symbol: str | None) -> dict[str, Any]:
        fs = _normalize_sym(focus_symbol or self._last_focus_symbol or "DOGEUSDT")
        out = dict(self._account)
        room = dict(self._rooms.get(fs, self._room_defaults(fs)))
        out.update(room)
        return out

    def rooms_view(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._rooms.items()}

    def snapshot_defaults(self) -> dict[str, Any]:
        return dict(_DEFAULT_STATE)

    async def merge_account(self, patch: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            for k, v in patch.items():
                if k in _ACCOUNT_KEYS and v is not None:
                    self._account[k] = v
            return dict(self.flat_state_unlocked())

    async def merge_room(self, symbol: str, patch: dict[str, Any]) -> dict[str, Any]:
        sym = _normalize_sym(symbol)
        async with self._lock:
            base = dict(self._rooms.get(sym, self._room_defaults(sym)))
            for k, v in patch.items():
                if k == "activeGridLines":
                    continue
                if k in _ACCOUNT_KEYS:
                    self._account[k] = v  # allow account keys to ride room patch
                    continue
                if v is not None:
                    base[k] = v
            if patch.get("generatorCount") is not None:
                base["activeGridLines"] = int(patch["generatorCount"])
            self._rooms[sym] = base
            self._last_focus_symbol = sym
            return dict(self.flat_state_unlocked(sym))

    async def merge_state(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Split account vs room (contractual dashboard keys)."""
        sym_raw = patch.get("symbol")
        sym_u = _normalize_sym(str(sym_raw)) if sym_raw else None
        async with self._lock:
            for k, v in patch.items():
                if k in _ACCOUNT_KEYS and v is not None:
                    self._account[k] = v

            keys_to_skip = _ACCOUNT_KEYS | {"symbol"}
            room_patch = {k: v for k, v in patch.items() if k not in keys_to_skip and v is not None}
            if room_patch:
                fs = sym_u or self._last_focus_symbol
                base = dict(self._rooms.get(fs, self._room_defaults(fs)))
                for k, v in room_patch.items():
                    if k == "activeGridLines":
                        continue
                    base[k] = v
                if patch.get("generatorCount") is not None:
                    base["activeGridLines"] = int(patch["generatorCount"])
                self._rooms[fs] = base
                if sym_u:
                    self._last_focus_symbol = sym_u

            focus = sym_u or self._last_focus_symbol
            return dict(self.flat_state_unlocked(focus))

    def flat_state_unlocked(self, focus_symbol: str | None = None) -> dict[str, Any]:
        fs = _normalize_sym(focus_symbol or self._last_focus_symbol or "DOGEUSDT")
        out = dict(self._account)
        room = dict(self._rooms.get(fs, self._room_defaults(fs)))
        out.update(room)
        return out

    async def remove_room(self, symbol: str) -> None:
        sym = _normalize_sym(symbol)
        async with self._lock:
            self._rooms.pop(sym, None)
            if self._last_focus_symbol == sym:
                self._last_focus_symbol = next(iter(sorted(self._rooms.keys())), "DOGEUSDT")

    async def replace_state(self, data: dict[str, Any]) -> None:
        async with self._lock:
            sym = _normalize_sym(str(data.get("symbol", "DOGEUSDT")))
            self._last_focus_symbol = sym
            merged_room = self._room_defaults(sym)
            merged_room.update(data)
            self._rooms[sym] = merged_room
            for k in _ACCOUNT_KEYS:
                if k in data:
                    self._account[k] = data[k]

    async def register(self, websocket: WebSocket, *, bot_id: str = "default") -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.append(websocket)
            snap = dict(self.flat_state_unlocked())
            snap["rooms"] = {k: dict(v) for k, v in self._rooms.items()}
            snap["account"] = dict(self._account)
        await apply_credentials_meta(bot_id, snap)
        await websocket.send_json({"type": "snapshot", "data": snap})

    def unregister(self, websocket: WebSocket) -> None:
        if websocket in self._clients:
            self._clients.remove(websocket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)

    async def broadcast_room(self, symbol: str, message: dict[str, Any]) -> None:
        sym = _normalize_sym(symbol)
        msg = dict(message)
        msg["symbol"] = sym
        await self.broadcast(msg)


hub = BotHub()
