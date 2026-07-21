"""Bearer token / session cookie authentication for /api routes."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

SESSION_COOKIE = "ytui_session"


def token_ok(request: Request) -> bool:
    """True when the request carries a valid Bearer header or session cookie."""
    expected: str = request.app.state.settings.api_token
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and secrets.compare_digest(token.encode(), expected.encode()):
        return True
    cookie = request.cookies.get(SESSION_COOKIE, "")
    return bool(cookie) and secrets.compare_digest(cookie.encode(), expected.encode())


async def require_token(request: Request) -> None:
    if not token_ok(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
