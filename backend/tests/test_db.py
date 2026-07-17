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
