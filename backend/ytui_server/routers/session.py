"""Web session login/logout via an HttpOnly cookie.

The browser SPA cannot attach an Authorization header to <video>/<track>/MSE
requests, so it authenticates every call with this cookie instead. TUI,
mobile and scripts keep using the Bearer header (see security.require_token,
which accepts either).
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, Response, status

from ..models import AuthStatusOut, SessionIn
from ..security import SESSION_COOKIE, token_ok

router = APIRouter()

_COOKIE_MAX_AGE = 365 * 24 * 3600


@router.post("/session", status_code=204)
async def login(body: SessionIn, request: Request, response: Response) -> None:
    expected: str = request.app.state.settings.api_token
    if not secrets.compare_digest(body.token.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        )
    response.set_cookie(
        SESSION_COOKIE,
        body.token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


@router.delete("/session", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/session", response_model=AuthStatusOut)
async def session_status(request: Request) -> AuthStatusOut:
    return AuthStatusOut(authenticated=token_ok(request))
