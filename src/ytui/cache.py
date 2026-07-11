"""SQLite metadata cache: feed items (TTL), handle resolutions, watch history,
local playlists."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
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
CREATE TABLE IF NOT EXISTS channel_names (
    channel_id TEXT PRIMARY KEY,
    title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watch_history (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    channel_title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'video',
    watched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS local_playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS local_playlist_items (
    playlist_id INTEGER NOT NULL REFERENCES local_playlists(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    channel_title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'video',
    PRIMARY KEY (playlist_id, position)
);
"""


class LocalPlaylist:
    """A local playlist row: id, name, creation time and item count."""

    def __init__(self, playlist_id: int, name: str, created_at: float, item_count: int) -> None:
        self.id = playlist_id
        self.name = name
        self.created_at = created_at
        self.item_count = item_count


class MetaCache:
    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            cache_dir().mkdir(parents=True, exist_ok=True)
            path = cache_dir() / "meta.sqlite"
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA foreign_keys = ON")
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

    # -- channel names (permanent, refreshed on each feed fetch) --

    def get_channel_name(self, channel_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT title FROM channel_names WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        if row:
            return row[0]
        # Backfill from cached feed items (works even when the feed was
        # served from cache and no fresh RSS fetch happened).
        videos = self.get_feed(channel_id, allow_stale=True)
        if videos and videos[0].channel_title:
            title = videos[0].channel_title
            self.set_channel_name(channel_id, title)
            return title
        return None

    def set_channel_name(self, channel_id: str, title: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO channel_names (channel_id, title) VALUES (?, ?)",
            (channel_id, title),
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
        if videos and videos[0].channel_title:
            self.set_channel_name(channel_id, videos[0].channel_title)

    def find_cached_video(self, video_id: str) -> Video | None:
        """Look up a video by id across all cached feeds (stale included)."""
        rows = self._conn.execute("SELECT videos_json FROM feed_items").fetchall()
        for (videos_json,) in rows:
            for v in json.loads(videos_json):
                if v.get("video_id") == video_id:
                    return Video.model_validate(v)
        return None

    def clear_feed(self) -> None:
        self._conn.execute("DELETE FROM feed_items")
        self._conn.commit()

    # -- watch history --

    def record_watch(self, video: Video) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO watch_history "
            "(video_id, title, channel_title, kind, watched_at) VALUES (?, ?, ?, ?, ?)",
            (video.video_id, video.title, video.channel_title, video.kind, time.time()),
        )
        self._conn.commit()

    def watched_ids(self) -> set[str]:
        rows = self._conn.execute("SELECT video_id FROM watch_history").fetchall()
        return {r[0] for r in rows}

    def watch_history(self, limit: int = 200) -> list[Video]:
        """Most recent watches first."""
        rows = self._conn.execute(
            "SELECT video_id, title, channel_title, kind, watched_at "
            "FROM watch_history ORDER BY watched_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        videos = []
        for video_id, title, channel_title, kind, watched_at in rows:
            thumb = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" if kind == "video" else ""
            videos.append(
                Video(
                    video_id=video_id,
                    title=title,
                    channel_title=channel_title,
                    kind=kind,
                    published=datetime.fromtimestamp(watched_at, tz=timezone.utc),
                    thumbnail_url=thumb,
                )
            )
        return videos

    def remove_watch(self, video_id: str) -> None:
        self._conn.execute("DELETE FROM watch_history WHERE video_id = ?", (video_id,))
        self._conn.commit()

    # -- local playlists --

    def create_playlist(self, name: str) -> int | None:
        """Create a playlist. Returns its id, or None if the name is taken."""
        try:
            cur = self._conn.execute(
                "INSERT INTO local_playlists (name, created_at) VALUES (?, ?)",
                (name, time.time()),
            )
        except sqlite3.IntegrityError:
            return None
        self._conn.commit()
        return cur.lastrowid

    def rename_playlist(self, playlist_id: int, name: str) -> bool:
        try:
            self._conn.execute(
                "UPDATE local_playlists SET name = ? WHERE id = ?", (name, playlist_id)
            )
        except sqlite3.IntegrityError:
            return False
        self._conn.commit()
        return True

    def delete_playlist(self, playlist_id: int) -> None:
        self._conn.execute("DELETE FROM local_playlists WHERE id = ?", (playlist_id,))
        self._conn.commit()

    def list_playlists(self) -> list[LocalPlaylist]:
        rows = self._conn.execute(
            "SELECT p.id, p.name, p.created_at, COUNT(i.video_id) "
            "FROM local_playlists p LEFT JOIN local_playlist_items i ON i.playlist_id = p.id "
            "GROUP BY p.id ORDER BY p.name"
        ).fetchall()
        return [LocalPlaylist(*row) for row in rows]

    def add_playlist_item(self, playlist_id: int, video: Video) -> bool:
        """Append an item to a playlist. Returns False if already present."""
        exists = self._conn.execute(
            "SELECT 1 FROM local_playlist_items WHERE playlist_id = ? AND video_id = ?",
            (playlist_id, video.video_id),
        ).fetchone()
        if exists:
            return False
        row = self._conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM local_playlist_items "
            "WHERE playlist_id = ?",
            (playlist_id,),
        ).fetchone()
        self._conn.execute(
            "INSERT INTO local_playlist_items "
            "(playlist_id, position, video_id, title, channel_title, url, kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                playlist_id,
                row[0],
                video.video_id,
                video.title,
                video.channel_title,
                video.url,
                video.kind,
            ),
        )
        self._conn.commit()
        return True

    def playlist_items(self, playlist_id: int) -> list[Video]:
        rows = self._conn.execute(
            "SELECT video_id, title, channel_title, kind FROM local_playlist_items "
            "WHERE playlist_id = ? ORDER BY position",
            (playlist_id,),
        ).fetchall()
        videos = []
        for video_id, title, channel_title, kind in rows:
            thumb = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" if kind == "video" else ""
            videos.append(
                Video(
                    video_id=video_id,
                    title=title,
                    channel_title=channel_title,
                    kind=kind,
                    thumbnail_url=thumb,
                )
            )
        return videos

    def remove_playlist_item(self, playlist_id: int, video_id: str) -> None:
        """Remove an item and compact positions to keep the ordering dense."""
        self._conn.execute(
            "DELETE FROM local_playlist_items WHERE playlist_id = ? AND video_id = ?",
            (playlist_id, video_id),
        )
        rows = self._conn.execute(
            "SELECT position FROM local_playlist_items WHERE playlist_id = ? ORDER BY position",
            (playlist_id,),
        ).fetchall()
        for new_pos, (old_pos,) in enumerate(rows):
            if new_pos != old_pos:
                self._conn.execute(
                    "UPDATE local_playlist_items SET position = ? "
                    "WHERE playlist_id = ? AND position = ?",
                    (new_pos, playlist_id, old_pos),
                )
        self._conn.commit()
