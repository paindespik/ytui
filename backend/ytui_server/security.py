"""Bearer token authentication for /api routes."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status


async def require_token(request: Request) -> None:
    expected: str = request.app.state.settings.api_token
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
