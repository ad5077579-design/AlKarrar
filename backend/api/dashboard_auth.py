"""Optional dashboard login (single operator) — cookie session, no Binance keys in browser."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from http.cookies import SimpleCookie
from typing import Mapping

from starlette.responses import Response

COOKIE_NAME = "alkarrar_session"
DEFAULT_SESSION_HOURS = 168  # 7 days


def _env_password() -> str:
    return (
        os.getenv("ALKARRAR_DASHBOARD_PASSWORD")
        or os.getenv("ALKARRAR_AUTH_PASSWORD")
        or ""
    ).strip()


def _env_username() -> str:
    return (
        os.getenv("ALKARRAR_DASHBOARD_USERNAME")
        or os.getenv("ALKARRAR_AUTH_USERNAME")
        or "admin"
    ).strip() or "admin"


def auth_enabled() -> bool:
    """When a dashboard password is set, API + WS require a valid session."""
    explicit = (os.getenv("ALKARRAR_AUTH_ENABLED") or "").strip().lower()
    if explicit in ("0", "false", "no", "off"):
        return False
    if explicit in ("1", "true", "yes", "on"):
        return bool(_env_password())
    return bool(_env_password())


def _signing_secret() -> bytes:
    raw = (os.getenv("ALKARRAR_AUTH_SECRET") or _env_password() or "").strip()
    if not raw:
        raw = "alkarrar-dev-insecure-change-me"
    return raw.encode("utf-8")


def session_ttl_seconds() -> int:
    try:
        hours = float(os.getenv("ALKARRAR_AUTH_SESSION_HOURS", str(DEFAULT_SESSION_HOURS)))
    except (TypeError, ValueError):
        hours = float(DEFAULT_SESSION_HOURS)
    return max(3600, int(hours * 3600))


def verify_login(username: str, password: str) -> bool:
    if not auth_enabled():
        return True
    return username.strip() == _env_username() and password == _env_password()


def create_session_token() -> str:
    exp = int(time.time()) + session_ttl_seconds()
    payload = str(exp)
    sig = hmac.new(_signing_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def validate_session_token(token: str | None) -> bool:
    if not auth_enabled():
        return True
    if not token or "." not in token:
        return False
    exp_s, sig = token.rsplit(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    expected = hmac.new(_signing_secret(), exp_s.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    return time.time() <= exp


def parse_cookie_header(cookie_header: str | None, name: str = COOKIE_NAME) -> str | None:
    if not cookie_header:
        return None
    jar = SimpleCookie()
    jar.load(cookie_header)
    morsel = jar.get(name)
    return morsel.value if morsel else None


def token_from_scope_headers(headers: Mapping[bytes, bytes]) -> str | None:
    raw = headers.get(b"cookie", b"").decode("latin-1", errors="ignore")
    return parse_cookie_header(raw)


def set_session_cookie(response: Response, token: str) -> None:
    secure = (os.getenv("ALKARRAR_AUTH_COOKIE_SECURE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=session_ttl_seconds(),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def is_public_http_path(path: str, method: str) -> bool:
    method = method.upper()
    if path == "/api/auth/login" and method == "POST":
        return True
    if path == "/api/auth/status" and method == "GET":
        return True
    return False
