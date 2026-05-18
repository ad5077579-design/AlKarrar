"""Reject unauthenticated HTTP requests when dashboard password is configured."""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.api.dashboard_auth import (
    auth_enabled,
    is_public_http_path,
    parse_cookie_header,
    validate_session_token,
)


class DashboardAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        method = scope.get("method") or "GET"

        if method.upper() == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if not path.startswith("/api") or not auth_enabled():
            await self.app(scope, receive, send)
            return

        if is_public_http_path(path, method):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        token = parse_cookie_header(headers.get("cookie"))
        if validate_session_token(token):
            await self.app(scope, receive, send)
            return

        body = json.dumps({"detail": "Authentication required"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
