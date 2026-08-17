"""Database-level tests: pragmas, migrations, history pruning."""

from __future__ import annotations

from pathlib import Path

from conftest import make_video

from ytui_server.db import _MIGRATIONS, Database


def test_migrations_applied_on_fresh_db(tmp_path: Path) -> None:
    db = Database(tmp_path / "meta.sqlite")
    try:
        version = db._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == len(_MIGRATIONS)
        index = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_watch_history_watched_at'"
        ).fetchone()
        assert index is not None
        journal_mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode == "wal"
    finally:
        db.close()


def test_migrations_idempotent_on_reopen(tmp_path: Path) -> None:
    path = tmp_path / "meta.sqlite"
    Database(path).close()
    db = Database(path)
    try:
        version = db._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == len(_MIGRATIONS)
    finally:
        db.close()


def test_record_watch_prunes_history(tmp_path: Path) -> None:
    db = Database(tmp_path / "meta.sqlite", history_max_rows=5)
    try:
        for i in range(7):
            db.record_watch(make_video(f"video{i:06d}"))
        entries = db.watch_history(limit=100)
        assert len(entries) == 5
        kept_ids = {video.video_id for video, _, _ in entries}
        assert kept_ids == {f"video{i:06d}" for i in range(2, 7)}
    finally:
        db.close()


def test_record_watch_unlimited_by_default(tmp_path: Path) -> None:
    db = Database(tmp_path / "meta.sqlite")
    try:
        for i in range(7):
            db.record_watch(make_video(f"video{i:06d}"))
        assert len(db.watch_history(limit=100)) == 7
    finally:
        db.close()


def test_record_watch_same_id_two_platforms(tmp_path: Path) -> None:
    import sqlite3  # noqa: F401  (keep import parity with legacy helper below)

    from ytui_server.models import Video

    db = Database(tmp_path / "meta.sqlite")
    try:
        db.record_watch(make_video("dQw4w9WgXcQ"))
        db.record_watch(
            Video(video_id="dQw4w9WgXcQ", title="CB video", platform="crowdbunker")
        )
        # Two rows: the bare id is the same on both platforms.
        assert len(db.watch_history(limit=100)) == 2
        # Re-watching the same (platform, id) stays one row.
        db.record_watch(make_video("dQw4w9WgXcQ", title="New title"))
        assert len(db.watch_history(limit=100)) == 2
        # Independent resume points per platform.
        db.save_position("youtube", "dQw4w9WgXcQ", 10.0, 100.0)
        db.save_position("crowdbunker", "dQw4w9WgXcQ", 50.0, 80.0)
        assert db.get_resume("youtube", "dQw4w9WgXcQ") == (10.0, 100.0, "")
        assert db.get_resume("crowdbunker", "dQw4w9WgXcQ") == (50.0, 80.0, "")
        ids = db.watched_ids()
        assert "youtube:dQw4w9WgXcQ" in ids and "crowdbunker:dQw4w9WgXcQ" in ids
        assert db.remove_watch("crowdbunker", "dQw4w9WgXcQ")
        assert db.get_resume("crowdbunker", "dQw4w9WgXcQ") is None
        assert db.get_resume("youtube", "dQw4w9WgXcQ") is not None
    finally:
        db.close()


def _make_legacy_db(path: Path) -> None:
    """A pre-migration-3 database: watch_history with a bare video_id PK."""
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE watch_history (
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
        CREATE INDEX idx_watch_history_watched_at
            ON watch_history(watched_at DESC);
        CREATE TABLE sponsor_segments (
            video_id TEXT PRIMARY KEY,
            fetched_at REAL NOT NULL,
            segments_json TEXT NOT NULL
        );
        """
    )
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        "INSERT INTO watch_history "
        "(video_id, title, platform, watched_at, position, duration) "
        "VALUES ('abc123', 'Old video', 'youtube', 1000.0, 42.0, 600.0)"
    )
    conn.execute(
        "INSERT INTO watch_history (video_id, title, platform, watched_at) "
        "VALUES ('cbuid111', 'CB video', 'crowdbunker', 2000.0)"
    )
    conn.commit()
    conn.close()


def test_migration3_preserves_history(tmp_path: Path) -> None:
    path = tmp_path / "meta.sqlite"
    _make_legacy_db(path)
    db = Database(path)
    try:
        version = db._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == len(_MIGRATIONS)
        entries = db.watch_history(limit=100)
        assert len(entries) == 2
        by_id = {video.video_id: (video, watched_at, pos) for video, watched_at, pos in entries}
        assert by_id["abc123"][2] == 42.0
        assert by_id["abc123"][0].platform == "youtube"
        assert by_id["cbuid111"][0].platform == "crowdbunker"
        # Composite primary key (platform, video_id): pk is the 1-based
        # position of the column inside the primary key (0 = not in the PK).
        pk_by_name = {
            row[1]: row[5]
            for row in db._conn.execute("PRAGMA table_info(watch_history)").fetchall()
        }
        assert pk_by_name["platform"] == 1
        assert pk_by_name["video_id"] == 2
    finally:
        db.close()
