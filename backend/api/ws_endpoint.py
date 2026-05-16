"""WebSocket endpoint mounted at ``/ws`` (not under ``/api``)."""

from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from backend.api.bot_hub import hub

_log = logging.getLogger(__name__)


async def websocket_dashboard(ws: WebSocket) -> None:
    await hub.register(ws)
    try:
        while True:
            raw = await ws.receive_text()
            if raw.strip().lower() in ("ping", '{"type":"ping"}'):
                await ws.send_text("pong")
    except WebSocketDisconnect:
        _log.debug("ws client disconnected")
    finally:
        hub.unregister(ws)
