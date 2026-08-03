"""YouTube account credentials pushed from the desktop client.

Two independent artefacts: the OAuth token (ratings, comments) and the browser
cookies (personalised home feed only).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..models import YouTubeAuthStatusOut
from ..services import ytdlp
from ..services.cookies import CookieError
from ..services.youtube import AuthError

router = APIRouter()


@router.get("/auth/youtube/status", response_model=YouTubeAuthStatusOut)
async def auth_status(request: Request) -> YouTubeAuthStatusOut:
    return YouTubeAuthStatusOut(
        authenticated=request.app.state.youtube_service.is_authenticated(),
        cookies=request.app.state.youtube_cookies.exists(),
    )


@router.post("/auth/youtube/token", status_code=204)
async def push_token(request: Request) -> None:
    body = await request.body()
    if len(body) > 64 * 1024:
        raise HTTPException(status_code=413, detail="Token payload too large")
    try:
        request.app.state.youtube_service.save_token(body.decode("utf-8"))
    except (AuthError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/auth/youtube/cookies", status_code=204)
async def push_cookies(request: Request) -> None:
    """Store the account cookies, but only after YouTube accepts them."""
    body = await request.body()
    # A real youtube.com-only export measures ~350 KB: YouTube keeps a ~2.8 KB
    # `ST-*` widget cookie per embed. The cap only rejects absurd payloads.
    if len(body) > 4 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Cookie payload too large")
    store = request.app.state.youtube_cookies
    try:
        store.save(body.decode("utf-8"))
    except (CookieError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        videos = await ytdlp.home_recommendations(store.path, limit=1)
    except ytdlp.UpstreamError as exc:
        store.clear()
        raise HTTPException(
            status_code=422, detail=f"YouTube refused the cookies: {exc}"
        ) from exc
    if not videos:
        store.clear()
        raise HTTPException(
            status_code=422,
            detail=(
                "YouTube does not recognise this session. Export the cookies from a "
                "private window and close it without browsing further."
            ),
        )


@router.delete("/auth/youtube/cookies", status_code=204)
async def delete_cookies(request: Request) -> None:
    """Idempotent: removing absent cookies is not an error."""
    request.app.state.youtube_cookies.clear()
