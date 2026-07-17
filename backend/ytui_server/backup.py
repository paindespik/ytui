"""Online SQLite backup with retention.

Usage: python -m ytui_server.backup [--data-dir DIR] [--dest DIR] [--keep-days N]

Runs inside the backend container (no host tooling needed); uses the sqlite3
backup API, safe against a live WAL-mode database.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


def run_backup(data_dir: Path, dest: Path, keep_days: int) -> Path:
    """Snapshot data_dir/meta.sqlite into dest and purge old backups.

    Returns the created backup path. Raises FileNotFoundError if the source
    database does not exist.
    """
    db = data_dir / "meta.sqlite"
    if not db.exists():
        raise FileNotFoundError(f"database not found: {db}")
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"meta-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite"

    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    purged = 0
    cutoff = time.time() - keep_days * 86400
    for old in dest.glob("meta-*.sqlite"):
        if old == target:
            continue
        if old.stat().st_mtime < cutoff:
            old.unlink()
            purged += 1
    print(f"backup written: {target} ({purged} old backup(s) purged)")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("YTUI_DATA_DIR", "/data")),
        help="directory containing meta.sqlite (default: $YTUI_DATA_DIR or /data)",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="backup directory (default: DATA_DIR/backups)",
    )
    parser.add_argument(
        "--keep-days",
        type=int,
        default=14,
        help="delete backups older than this many days (default: 14)",
    )
    args = parser.parse_args(argv)
    dest = args.dest if args.dest is not None else args.data_dir / "backups"
    try:
        run_backup(args.data_dir, dest, args.keep_days)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
