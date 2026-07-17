"""Online backup: snapshot correctness and retention."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest
from conftest import make_video

from ytui_server.backup import main, run_backup
from ytui_server.db import Database


def test_run_backup_snapshots_live_db(tmp_path: Path) -> None:
    db = Database(tmp_path / "meta.sqlite")
    try:
        db.record_watch(make_video("video000001"))
        db.record_watch(make_video("video000002"))
        target = run_backup(tmp_path, tmp_path / "backups", keep_days=14)
    finally:
        db.close()
    assert target.exists()
    conn = sqlite3.connect(target)
    try:
        count = conn.execute("SELECT COUNT(*) FROM watch_history").fetchone()[0]
    finally:
        conn.close()
    assert count == 2


def test_run_backup_purges_old_backups(tmp_path: Path) -> None:
    db = Database(tmp_path / "meta.sqlite")
    db.close()
    dest = tmp_path / "backups"
    dest.mkdir()
    old = dest / "meta-20200101-000000.sqlite"
    old.write_bytes(b"stale")
    stale_mtime = time.time() - 30 * 86400
    os.utime(old, (stale_mtime, stale_mtime))
    fresh = dest / "meta-20990101-000000.sqlite"
    fresh.write_bytes(b"fresh")

    run_backup(tmp_path, dest, keep_days=14)

    assert not old.exists()
    assert fresh.exists()


def test_run_backup_missing_db(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        run_backup(tmp_path, tmp_path / "backups", keep_days=14)


def test_main_missing_db_returns_1(tmp_path: Path, capsys) -> None:
    assert main(["--data-dir", str(tmp_path)]) == 1
    assert "database not found" in capsys.readouterr().err
