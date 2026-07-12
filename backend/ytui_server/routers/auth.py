"""YouTube OAuth token management (token pushed from the desktop client)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..models import AuthStatusOut
from ..services.youtube import AuthError

router = APIRouter()


@router.get("/auth/youtube/status", response_model=AuthStatusOut)
async def auth_status(request: Request) -> AuthStatusOut:
    return AuthStatusOut(authenticated=request.app.state.youtube_service.is_authenticated())


@router.post("/auth/youtube/token", status_code=204)
async def push_token(request: Request) -> None:
    body = await request.body()
    if len(body) > 64 * 1024:
        raise HTTPException(status_code=413, detail="Token payload too large")
    try:
        request.app.state.youtube_service.save_token(body.decode("utf-8"))
    except (AuthError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
