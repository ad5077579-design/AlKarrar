"""In-memory bot runtime + WebSocket fan-out (BFF layer for dashboard)."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

_DEFAULT_STATE: dict[str, Any] = {
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
    "orders": [],
    "syncError": "",
    "syncOkAt": "",
    "exchangeTestnet": False,
}


class BotHub:
    """Single-process hub; extend with Redis if you scale horizontally."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state: dict[str, Any] = dict(_DEFAULT_STATE)
        self._clients: list[WebSocket] = []

    @property
    def state(self) -> dict[str, Any]:
        return dict(self._state)

    async def merge_state(self, patch: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            for k, v in patch.items():
                if k == "activeGridLines":
                    continue
                if v is not None:
                    self._state[k] = v
            if patch.get("generatorCount") is not None:
                self._state["activeGridLines"] = int(patch["generatorCount"])
            return dict(self._state)

    def snapshot_defaults(self) -> dict[str, Any]:
        return dict(_DEFAULT_STATE)

    async def replace_state(self, data: dict[str, Any]) -> None:
        async with self._lock:
            self._state = {**_DEFAULT_STATE, **data}

    async def register(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.append(websocket)
            snap = dict(self._state)
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


hub = BotHub()
