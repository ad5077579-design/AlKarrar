"""Dashboard login / logout (protects trading UI when ALKARRAR_DASHBOARD_PASSWORD is set)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.api.dashboard_auth import (
    auth_enabled,
    clear_session_cookie,
    create_session_token,
    parse_cookie_header,
    set_session_cookie,
    validate_session_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(default="admin", max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.get("/status")
async def auth_status(request: Request) -> dict[str, bool]:
    token = parse_cookie_header(request.headers.get("cookie"))
    return {
        "authRequired": auth_enabled(),
        "authenticated": validate_session_token(token),
    }


@router.post("/login")
async def login(body: LoginBody, response: Response) -> dict[str, bool]:
    if not auth_enabled():
        return {"ok": True, "authRequired": False}
    from backend.api.dashboard_auth import verify_login

    if not verify_login(body.username, body.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    set_session_cookie(response, create_session_token())
    return {"ok": True, "authRequired": True}


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    clear_session_cookie(response)
    return {"ok": True}
