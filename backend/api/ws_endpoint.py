"""WebSocket endpoint mounted at ``/ws`` (not under ``/api``)."""

from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from backend.api.bot_hub import hub
from backend.api.dashboard_auth import auth_enabled, parse_cookie_header, validate_session_token

_log = logging.getLogger(__name__)


async def websocket_dashboard(ws: WebSocket) -> None:
    if auth_enabled():
        token = parse_cookie_header(ws.headers.get("cookie"))
        if not validate_session_token(token):
            await ws.accept()
            await ws.close(code=1008, reason="Authentication required")
            return
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
