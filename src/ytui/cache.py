"""SQLite metadata cache: feed items (TTL) and handle -> channel_id resolutions."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .config import cache_dir
from .models import Video

FEED_TTL_SECONDS = 15 * 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_items (
    channel_id TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    videos_json TEXT NOT NULL,
    PRIMARY KEY (channel_id)
);
CREATE TABLE IF NOT EXISTS handle_resolutions (
    handle TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL
);
"""


class MetaCache:
    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            cache_dir().mkdir(parents=True, exist_ok=True)
            path = cache_dir() / "meta.sqlite"
        self._conn = sqlite3.connect(path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- handle resolutions (permanent) --

    def get_handle(self, handle: str) -> str | None:
        row = self._conn.execute(
            "SELECT channel_id FROM handle_resolutions WHERE handle = ?", (handle,)
        ).fetchone()
        return row[0] if row else None

    def set_handle(self, handle: str, channel_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO handle_resolutions (handle, channel_id) VALUES (?, ?)",
            (handle, channel_id),
        )
        self._conn.commit()

    # -- feed items (TTL, with stale fallback for offline mode) --

    def get_feed(self, channel_id: str, allow_stale: bool = False) -> list[Video] | None:
        row = self._conn.execute(
            "SELECT fetched_at, videos_json FROM feed_items WHERE channel_id = ?",
            (channel_id,),
        ).fetchone()
        if row is None:
            return None
        fetched_at, videos_json = row
        if not allow_stale and time.time() - fetched_at > FEED_TTL_SECONDS:
            return None
        return [Video.model_validate(v) for v in json.loads(videos_json)]

    def set_feed(self, channel_id: str, videos: list[Video]) -> None:
        payload = json.dumps([v.model_dump(mode="json") for v in videos])
        self._conn.execute(
            "INSERT OR REPLACE INTO feed_items (channel_id, fetched_at, videos_json) "
            "VALUES (?, ?, ?)",
            (channel_id, time.time(), payload),
        )
        self._conn.commit()

    def clear_feed(self) -> None:
        self._conn.execute("DELETE FROM feed_items")
        self._conn.commit()
