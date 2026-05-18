"""Dashboard session cookie auth."""

from __future__ import annotations

import os

import pytest
from starlette.responses import Response

from backend.api.dashboard_auth import (
    COOKIE_NAME,
    auth_enabled,
    create_session_token,
    parse_cookie_header,
    set_session_cookie,
    validate_session_token,
    verify_login,
)


@pytest.fixture(autouse=True)
def _clear_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "ALKARRAR_DASHBOARD_PASSWORD",
        "ALKARRAR_AUTH_PASSWORD",
        "ALKARRAR_AUTH_ENABLED",
        "ALKARRAR_AUTH_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)


def test_auth_disabled_without_password() -> None:
    assert auth_enabled() is False
    assert validate_session_token(None) is True


def test_auth_enabled_with_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALKARRAR_DASHBOARD_PASSWORD", "secret-pass")
    assert auth_enabled() is True
    assert verify_login("admin", "secret-pass") is True
    assert verify_login("admin", "wrong") is False


def test_session_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALKARRAR_DASHBOARD_PASSWORD", "x")
    monkeypatch.setenv("ALKARRAR_AUTH_SECRET", "sign-key")
    token = create_session_token()
    assert validate_session_token(token) is True
    assert validate_session_token("bad.token") is False


def test_cookie_set_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALKARRAR_DASHBOARD_PASSWORD", "x")
    resp = Response()
    set_session_cookie(resp, "1.abc")
    raw = resp.headers.get("set-cookie", "")
    assert COOKIE_NAME in raw
    assert "httponly" in raw.lower()
    assert parse_cookie_header(f"{COOKIE_NAME}=1.abc") == "1.abc"
