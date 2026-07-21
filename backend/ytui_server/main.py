"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__
from .db import Database
from .routers import (
    auth,
    channels,
    feed,
    history,
    lives,
    playlists,
    proxy,
    session,
    status,
    suggestions,
    videos,
)
from .security import require_token
from .services.feed import FeedService
from .services.live import LiveMonitor
from .services.livechat import ChatManager
from .services.sponsorblock import SponsorBlockService
from .services.suggestions import SuggestionsService
from .services.youtube import YouTubeService
from .settings import Settings


class _WebStaticFiles(StaticFiles):
    """Static SPA files: always revalidate (cheap 304s via ETag).

    Without an explicit Cache-Control, browsers apply heuristic caching and
    may keep serving stale JS modules after a deploy.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Settings | None = None, start_live_poll: bool = True) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.started_at = time.monotonic()
        proxy_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(10.0, read=30.0),
            headers={"User-Agent": proxy.PROXY_UA},
        )
        app.state.proxy_client = proxy_client
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(
            settings.data_dir / "meta.sqlite",
            feed_ttl_seconds=settings.feed_ttl_minutes * 60,
            history_max_rows=settings.history_max_rows,
        )
        app.state.db = db
        app.state.feed_service = FeedService(db)
        app.state.suggestions_service = SuggestionsService(
            db, ttl_seconds=settings.suggestions_ttl_minutes * 60
        )
        app.state.youtube_service = YouTubeService(settings.data_dir)
        app.state.sponsorblock = SponsorBlockService(
            db,
            categories=[
                c.strip()
                for c in settings.sponsorblock_categories.split(",")
                if c.strip()
            ],
        )
        app.state.live_monitor = LiveMonitor(
            db,
            check_minutes=settings.live_check_minutes,
            twitch_check_seconds=settings.twitch_check_seconds,
            tiktok_check_seconds=settings.tiktok_check_seconds,
        )
        app.state.chat_manager = ChatManager()
        if start_live_poll:
            app.state.live_monitor.start()
        try:
            yield
        finally:
            await app.state.chat_manager.shutdown()
            await app.state.live_monitor.stop()
            await proxy_client.aclose()
            db.close()

    app = FastAPI(title="ytui-server", version=__version__, lifespan=lifespan)
    app.state.settings = settings

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    for router in (
        feed,
        suggestions,
        videos,
        channels,
        history,
        playlists,
        lives,
        auth,
        status,
        proxy,
    ):
        app.include_router(
            router.router, prefix="/api", dependencies=[Depends(require_token)]
        )
    app.include_router(session.router, prefix="/api")

    web_dir = Path(__file__).parent / "web"
    if web_dir.is_dir():
        app.mount("/", _WebStaticFiles(directory=web_dir, html=True), name="web")
    return app


def __getattr__(name: str):
    # Lazy so that importing create_app in tests doesn't require YTUI_API_TOKEN.
    if name == "app":
        return create_app()
    raise AttributeError(name)
