"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from . import __version__
from .db import Database
from .routers import auth, channels, feed, history, lives, playlists, suggestions, videos
from .security import require_token
from .services.feed import FeedService
from .services.suggestions import SuggestionsService
from .services.live import LiveMonitor
from .services.youtube import YouTubeService
from .settings import Settings


def create_app(settings: Settings | None = None, start_live_poll: bool = True) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(
            settings.data_dir / "meta.sqlite",
            feed_ttl_seconds=settings.feed_ttl_minutes * 60,
        )
        app.state.db = db
        app.state.feed_service = FeedService(db)
        app.state.suggestions_service = SuggestionsService(
            db, ttl_seconds=settings.suggestions_ttl_minutes * 60
        )
        app.state.youtube_service = YouTubeService(settings.data_dir)
        app.state.live_monitor = LiveMonitor(db, check_minutes=settings.live_check_minutes)
        if start_live_poll:
            app.state.live_monitor.start()
        try:
            yield
        finally:
            await app.state.live_monitor.stop()
            db.close()

    app = FastAPI(title="ytui-server", version=__version__, lifespan=lifespan)
    app.state.settings = settings

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    for router in (feed, suggestions, videos, channels, history, playlists, lives, auth):
        app.include_router(
            router.router, prefix="/api", dependencies=[Depends(require_token)]
        )
    return app


def __getattr__(name: str):
    # Lazy so that importing create_app in tests doesn't require YTUI_API_TOKEN.
    if name == "app":
        return create_app()
    raise AttributeError(name)
