"""SQLite storage: feed cache (TTL), handle resolutions, watch history,
local playlists and followed channels (server-side, replaces config.toml)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .models import FollowedChannel, Video

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
CREATE TABLE IF NOT EXISTS channels (
    ref TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT 'youtube',
    added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS watch_history (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    channel_title TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'video',
    platform TEXT NOT NULL DEFAULT 'youtube',
    watched_at REAL NOT NULL,
    position REAL NOT NULL DEFAULT 0,
    duration REAL,
    playlist_id TEXT NOT NULL DEFAULT '',
    channel_id TEXT NOT NULL DEFAULT ''
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
    platform TEXT NOT NULL DEFAULT 'youtube',
    PRIMARY KEY (playlist_id, position)
);
"""

# Each entry moves PRAGMA user_version from i to i+1. Never modify a shipped
# entry; append a new one at the end.
_MIGRATIONS: list[str] = [
    # 1: watch-history sort index (ORDER BY watched_at DESC without full scan)
    """
    CREATE INDEX IF NOT EXISTS idx_watch_history_watched_at
        ON watch_history(watched_at DESC);
    """,
    # 2: SponsorBlock segment cache
    """
    CREATE TABLE IF NOT EXISTS sponsor_segments (
        video_id TEXT PRIMARY KEY,
        fetched_at REAL NOT NULL,
        segments_json TEXT NOT NULL
    );
    """,
    # 3: watch history keyed by (platform, video_id): the same bare id can exist
    # on several platforms (an 11-char CrowdBunker uid is shaped like a YouTube
    # id), so a bare-id PK made histories and resume points collide. Rebuild the
    # table; existing rows keep their stored platform.
    """
    CREATE TABLE watch_history_new (
        video_id TEXT NOT NULL,
        title TEXT NOT NULL,
        channel_title TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL DEFAULT 'video',
        platform TEXT NOT NULL DEFAULT 'youtube',
        watched_at REAL NOT NULL,
        position REAL NOT NULL DEFAULT 0,
        duration REAL,
        playlist_id TEXT NOT NULL DEFAULT '',
        channel_id TEXT NOT NULL DEFAULT '',
        thumbnail_url TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (platform, video_id)
    );
    INSERT INTO watch_history_new
        (video_id, title, channel_title, kind, platform, watched_at, position,
         duration, playlist_id, channel_id)
        SELECT video_id, title, channel_title, kind, platform, watched_at, position,
               duration, playlist_id, channel_id
        FROM watch_history;
    DROP TABLE watch_history;
    ALTER TABLE watch_history_new RENAME TO watch_history;
    CREATE INDEX idx_watch_history_watched_at
        ON watch_history(watched_at DESC);
    """,
    # 4: persisted thumbnails for local playlist items (non-YouTube platforms
    # have no derivable thumbnail URL, so listings used to lose them).
    """
    ALTER TABLE local_playlist_items ADD COLUMN thumbnail_url TEXT NOT NULL DEFAULT '';
    """,
]


class PlaylistRow:
    def __init__(self, playlist_id: int, name: str, created_at: float, item_count: int) -> None:
        self.id = playlist_id
        self.name = name
        self.created_at = created_at
        self.item_count = item_count


class Database:
    """Thread-safe SQLite wrapper (single connection + lock; calls are fast)."""

    def __init__(
        self,
        path: Path,
        feed_ttl_seconds: int = 15 * 60,
        history_max_rows: int = 0,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._path = path
        self.feed_ttl_seconds = feed_ttl_seconds
        self.history_max_rows = history_max_rows
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for i, script in enumerate(_MIGRATIONS[version:], start=version + 1):
                self._conn.executescript(script)
                self._conn.execute(f"PRAGMA user_version = {i}")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    _FEED_KEY_PLATFORMS = frozenset(
        {"bitchute", "odysee", "twitch", "tiktok", "crowdbunker"}
    )

    def stats(self) -> dict:
        """Row counts and feed-cache freshness for the status endpoint."""
        with self._lock:
            history = self._conn.execute(
                "SELECT COUNT(*) FROM watch_history"
            ).fetchone()[0]
            channels = self._conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
            channels_by_platform = dict(
                self._conn.execute(
                    "SELECT platform, COUNT(*) FROM channels GROUP BY platform"
                ).fetchall()
            )
            cached, newest, oldest = self._conn.execute(
                "SELECT COUNT(*), MAX(fetched_at), MIN(fetched_at) FROM feed_items"
            ).fetchone()
            feed_rows = self._conn.execute(
                "SELECT channel_id, fetched_at FROM feed_items"
            ).fetchall()
        # Feed cache keys are '{platform}:{channel_id}' for non-YouTube
        # platforms and the bare channel id for YouTube.
        feed_newest_by_platform: dict[str, float] = {}
        for key, fetched_at in feed_rows:
            prefix = key.split(":", 1)[0]
            platform = prefix if prefix in self._FEED_KEY_PLATFORMS else "youtube"
            if fetched_at > feed_newest_by_platform.get(platform, 0.0):
                feed_newest_by_platform[platform] = fetched_at
        return {
            "history_rows": history,
            "channels": channels,
            "channels_by_platform": channels_by_platform,
            "feed_cached_channels": cached,
            "feed_newest_fetched_at": newest,
            "feed_oldest_fetched_at": oldest,
            "feed_newest_by_platform": feed_newest_by_platform,
            "db_bytes": self._path.stat().st_size if self._path.exists() else 0,
        }

    # -- handle resolutions --

    def get_handle(self, handle: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT channel_id FROM handle_resolutions WHERE handle = ?", (handle,)
            ).fetchone()
        return row[0] if row else None

    def set_handle(self, handle: str, channel_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO handle_resolutions (handle, channel_id) VALUES (?, ?)",
                (handle, channel_id),
            )
            self._conn.commit()

    # -- channel names --

    def get_channel_name(self, channel_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT title FROM channel_names WHERE channel_id = ?", (channel_id,)
            ).fetchone()
        if row:
            return row[0]
        videos = self.get_feed(channel_id, allow_stale=True)
        if videos and videos[0].channel_title:
            title = videos[0].channel_title
            self.set_channel_name(channel_id, title)
            return title
        return None

    def set_channel_name(self, channel_id: str, title: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO channel_names (channel_id, title) VALUES (?, ?)",
                (channel_id, title),
            )
            self._conn.commit()

    # -- feed cache (TTL, with stale fallback for upstream failures) --

    def get_feed(
        self,
        channel_id: str,
        allow_stale: bool = False,
        ttl_seconds: int | None = None,
    ) -> list[Video] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT fetched_at, videos_json FROM feed_items WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
        if row is None:
            return None
        fetched_at, videos_json = row
        ttl = self.feed_ttl_seconds if ttl_seconds is None else ttl_seconds
        if not allow_stale and time.time() - fetched_at > ttl:
            return None
        return [Video.model_validate(v) for v in json.loads(videos_json)]

    def set_feed(self, channel_id: str, videos: list[Video]) -> None:
        payload = json.dumps(
            [v.model_dump(mode="json", exclude={"url"}) for v in videos]
        )
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO feed_items (channel_id, fetched_at, videos_json) "
                "VALUES (?, ?, ?)",
                (channel_id, time.time(), payload),
            )
            self._conn.commit()
        if videos and videos[0].channel_title:
            self.set_channel_name(channel_id, videos[0].channel_title)

    # -- sponsor segments (SponsorBlock cache) --

    def get_sponsor_segments(
        self, video_id: str, ttl_seconds: int, allow_stale: bool = False
    ) -> list[dict] | None:
        """Cached segments, or None if absent/expired (unless allow_stale)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT fetched_at, segments_json FROM sponsor_segments "
                "WHERE video_id = ?",
                (video_id,),
            ).fetchone()
        if row is None:
            return None
        fetched_at, segments_json = row
        if not allow_stale and time.time() - fetched_at > ttl_seconds:
            return None
        return json.loads(segments_json)

    def set_sponsor_segments(self, video_id: str, segments: list[dict]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sponsor_segments "
                "(video_id, fetched_at, segments_json) VALUES (?, ?, ?)",
                (video_id, time.time(), json.dumps(segments)),
            )
            self._conn.commit()

    # -- followed channels --

    def list_channels(self) -> list[FollowedChannel]:
        with self._lock:
            # Backfill titles for channels added by bare UC id before their
            # name was known (channel_names is populated on every feed fetch).
            cur = self._conn.execute(
                "UPDATE channels SET title = "
                "(SELECT title FROM channel_names n WHERE n.channel_id = channels.channel_id) "
                "WHERE title = '' "
                "AND channel_id IN (SELECT channel_id FROM channel_names)"
            )
            if cur.rowcount:
                self._conn.commit()
            rows = self._conn.execute(
                "SELECT ref, channel_id, title, platform FROM channels ORDER BY added_at"
            ).fetchall()
        return [
            FollowedChannel(ref=r[0], channel_id=r[1], title=r[2], platform=r[3]) for r in rows
        ]

    def add_channel(self, channel: FollowedChannel) -> bool:
        """Persist a followed channel. Returns False if already followed."""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO channels (ref, channel_id, title, platform, added_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        channel.ref,
                        channel.channel_id,
                        channel.title,
                        channel.platform,
                        time.time(),
                    ),
                )
            except sqlite3.IntegrityError:
                return False
            self._conn.commit()
        return True

    def remove_channel(self, channel_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM channels WHERE channel_id = ?", (channel_id,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    # -- watch history --

    def record_watch(self, video: Video) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO watch_history "
                "(video_id, title, channel_title, kind, platform, watched_at, "
                "playlist_id, channel_id, thumbnail_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(platform, video_id) DO UPDATE SET "
                "title = excluded.title, "
                "channel_title = excluded.channel_title, "
                "kind = excluded.kind, "
                "watched_at = excluded.watched_at, "
                "playlist_id = CASE WHEN excluded.playlist_id != '' "
                "THEN excluded.playlist_id ELSE playlist_id END, "
                "channel_id = CASE WHEN excluded.channel_id != '' "
                "THEN excluded.channel_id ELSE channel_id END, "
                "thumbnail_url = CASE WHEN excluded.thumbnail_url != '' "
                "THEN excluded.thumbnail_url ELSE thumbnail_url END",
                (
                    video.video_id,
                    video.title,
                    video.channel_title,
                    video.kind,
                    video.platform,
                    time.time(),
                    video.playlist_id,
                    video.channel_id,
                    video.thumbnail_url,
                ),
            )
            if self.history_max_rows > 0:
                self._conn.execute(
                    "DELETE FROM watch_history WHERE video_id IN ("
                    "SELECT video_id FROM watch_history "
                    "ORDER BY watched_at DESC LIMIT -1 OFFSET ?)",
                    (self.history_max_rows,),
                )
            self._conn.commit()

    def save_position(
        self, platform: str, video_id: str, position: float, duration: float | None
    ) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE watch_history SET position = ?, duration = ? "
                "WHERE platform = ? AND video_id = ?",
                (position, duration, platform, video_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def get_resume(
        self, platform: str, video_id: str
    ) -> tuple[float, float | None, str] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT position, duration, playlist_id FROM watch_history "
                "WHERE platform = ? AND video_id = ?",
                (platform, video_id),
            ).fetchone()
        return None if row is None else (row[0], row[1], row[2])

    def watched_ids(self) -> list[str]:
        """Qualification: '{platform}:{video_id}' — the bare id collides across
        platforms (e.g. CrowdBunker uids shaped like YouTube ids)."""
        with self._lock:
            rows = self._conn.execute("SELECT platform, video_id FROM watch_history").fetchall()
        return [f"{platform}:{video_id}" for platform, video_id in rows]

    def watch_history(self, limit: int = 200) -> list[tuple[Video, float, float]]:
        """Most recent watches first, as (video, watched_at, position) tuples."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT video_id, title, channel_title, kind, platform, watched_at, "
                "position, duration, playlist_id, channel_id, thumbnail_url "
                "FROM watch_history ORDER BY watched_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out: list[tuple[Video, float, float]] = []
        for vid, title, ch_title, kind, platform, watched_at, pos, dur, pl_id, ch_id, thumb in rows:
            if not thumb and kind == "video" and platform == "youtube":
                # Legacy rows recorded before thumbnails were persisted.
                thumb = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
            out.append(
                (
                    Video(
                        video_id=vid,
                        title=title,
                        channel_title=ch_title,
                        channel_id=ch_id,
                        kind=kind,
                        platform=platform,
                        duration=int(dur) if dur else None,
                        thumbnail_url=thumb,
                        playlist_id=pl_id,
                    ),
                    watched_at,
                    pos,
                )
            )
        return out

    def remove_watch(self, platform: str, video_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM watch_history WHERE platform = ? AND video_id = ?",
                (platform, video_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    # -- local playlists --

    def create_playlist(self, name: str) -> int | None:
        with self._lock:
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
        with self._lock:
            try:
                cur = self._conn.execute(
                    "UPDATE local_playlists SET name = ? WHERE id = ?", (name, playlist_id)
                )
            except sqlite3.IntegrityError:
                return False
            self._conn.commit()
        return cur.rowcount > 0

    def get_playlist(self, playlist_id: int) -> PlaylistRow | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT p.id, p.name, p.created_at, COUNT(i.video_id) "
                "FROM local_playlists p "
                "LEFT JOIN local_playlist_items i ON i.playlist_id = p.id "
                "WHERE p.id = ? GROUP BY p.id",
                (playlist_id,),
            ).fetchone()
        return PlaylistRow(*row) if row else None

    def delete_playlist(self, playlist_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM local_playlists WHERE id = ?", (playlist_id,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def list_playlists(self) -> list[PlaylistRow]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.id, p.name, p.created_at, COUNT(i.video_id) "
                "FROM local_playlists p "
                "LEFT JOIN local_playlist_items i ON i.playlist_id = p.id "
                "GROUP BY p.id ORDER BY p.name"
            ).fetchall()
        return [PlaylistRow(*row) for row in rows]

    def add_playlist_item(self, playlist_id: int, video: Video) -> bool:
        """Append an item. Returns False if already present."""
        with self._lock:
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
                "(playlist_id, position, video_id, title, channel_title, url, kind, "
                "platform, thumbnail_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    playlist_id,
                    row[0],
                    video.video_id,
                    video.title,
                    video.channel_title,
                    video.url,
                    video.kind,
                    video.platform,
                    video.thumbnail_url,
                ),
            )
            self._conn.commit()
        return True

    def add_playlist_items(self, playlist_id: int, videos: list[Video]) -> tuple[int, int]:
        """Append many items in one transaction: (added, skipped).

        Items already in the playlist — and duplicates inside the batch — are
        skipped instead of failing the whole import.
        """
        added = 0
        with self._lock:
            known = {
                row[0]
                for row in self._conn.execute(
                    "SELECT video_id FROM local_playlist_items WHERE playlist_id = ?",
                    (playlist_id,),
                )
            }
            row = self._conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM local_playlist_items "
                "WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchone()
            position = row[0]
            for video in videos:
                if video.video_id in known:
                    continue
                known.add(video.video_id)
                self._conn.execute(
                    "INSERT INTO local_playlist_items "
                    "(playlist_id, position, video_id, title, channel_title, url, kind, "
                    "platform, thumbnail_url) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        playlist_id,
                        position,
                        video.video_id,
                        video.title,
                        video.channel_title,
                        video.url,
                        video.kind,
                        video.platform,
                        video.thumbnail_url,
                    ),
                )
                position += 1
                added += 1
            self._conn.commit()
        return added, len(videos) - added

    def playlist_items(self, playlist_id: int) -> list[tuple[int, Video]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT position, video_id, title, channel_title, kind, platform, "
                "thumbnail_url "
                "FROM local_playlist_items WHERE playlist_id = ? ORDER BY position",
                (playlist_id,),
            ).fetchall()
        items: list[tuple[int, Video]] = []
        for position, video_id, title, channel_title, kind, platform, thumb in rows:
            if not thumb and kind == "video" and platform == "youtube":
                # Legacy rows added before thumbnails were persisted.
                thumb = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
            items.append(
                (
                    position,
                    Video(
                        video_id=video_id,
                        title=title,
                        channel_title=channel_title,
                        kind=kind,
                        platform=platform,
                        thumbnail_url=thumb,
                    ),
                )
            )
        return items

    def remove_playlist_item(self, playlist_id: int, position: int) -> bool:
        """Remove an item at a position and compact positions."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM local_playlist_items WHERE playlist_id = ? AND position = ?",
                (playlist_id, position),
            )
            if cur.rowcount == 0:
                self._conn.commit()
                return False
            rows = self._conn.execute(
                "SELECT position FROM local_playlist_items "
                "WHERE playlist_id = ? ORDER BY position",
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
        return True
