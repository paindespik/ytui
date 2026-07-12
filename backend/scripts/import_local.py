#!/usr/bin/env python3
"""One-shot import of a local ytui client database and config into the server DB.

Usage:
    python import_local.py --server-db /data/meta.sqlite \
        [--client-db ~/.cache/ytui/meta.sqlite] [--config ~/.config/ytui/config.toml]

Imports watch history, local playlists, handle resolutions, channel names and
followed channels ([channels].list). Existing server rows are kept (no overwrite).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ytui_server.db import Database  # noqa: E402
from ytui_server.models import FollowedChannel  # noqa: E402


def import_client_db(server: Database, client_path: Path) -> None:
    src = sqlite3.connect(client_path)
    conn = server._conn  # direct access for bulk copy

    for table, cols in (
        ("handle_resolutions", "handle, channel_id"),
        ("channel_names", "channel_id, title"),
    ):
        rows = src.execute(f"SELECT {cols} FROM {table}").fetchall()
        conn.executemany(
            f"INSERT OR IGNORE INTO {table} ({cols}) VALUES (?, ?)", rows
        )
        print(f"{table}: {len(rows)} rows")

    rows = src.execute(
        "SELECT video_id, title, channel_title, kind, watched_at, position, "
        "duration, playlist_id, channel_id FROM watch_history"
    ).fetchall()
    conn.executemany(
        "INSERT OR IGNORE INTO watch_history "
        "(video_id, title, channel_title, kind, watched_at, position, duration, "
        "playlist_id, channel_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    print(f"watch_history: {len(rows)} rows")

    playlists = src.execute("SELECT id, name, created_at FROM local_playlists").fetchall()
    id_map: dict[int, int] = {}
    for old_id, name, created_at in playlists:
        row = conn.execute(
            "SELECT id FROM local_playlists WHERE name = ?", (name,)
        ).fetchone()
        if row:
            id_map[old_id] = row[0]
            continue
        cur = conn.execute(
            "INSERT INTO local_playlists (name, created_at) VALUES (?, ?)",
            (name, created_at),
        )
        id_map[old_id] = cur.lastrowid
    print(f"local_playlists: {len(playlists)} rows")

    items = src.execute(
        "SELECT playlist_id, position, video_id, title, channel_title, url, kind "
        "FROM local_playlist_items ORDER BY playlist_id, position"
    ).fetchall()
    count = 0
    for old_pl, _pos, video_id, title, channel_title, url, kind in items:
        new_pl = id_map.get(old_pl)
        if new_pl is None:
            continue
        exists = conn.execute(
            "SELECT 1 FROM local_playlist_items WHERE playlist_id = ? AND video_id = ?",
            (new_pl, video_id),
        ).fetchone()
        if exists:
            continue
        next_pos = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM local_playlist_items "
            "WHERE playlist_id = ?",
            (new_pl,),
        ).fetchone()[0]
        platform = "bitchute" if "bitchute.com" in url else "youtube"
        conn.execute(
            "INSERT INTO local_playlist_items "
            "(playlist_id, position, video_id, title, channel_title, url, kind, platform) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_pl, next_pos, video_id, title, channel_title, url, kind, platform),
        )
        count += 1
    print(f"local_playlist_items: {count} rows")
    conn.commit()
    src.close()


def import_config_channels(server: Database, config_path: Path) -> None:
    with open(config_path, "rb") as fh:
        data = tomllib.load(fh)
    refs = data.get("channels", {}).get("list", [])
    added = 0
    for ref in refs:
        if ref.startswith("bitchute:"):
            slug = ref[len("bitchute:"):]
            channel = FollowedChannel(
                ref=ref, channel_id=slug, title=slug, platform="bitchute"
            )
        elif ref.startswith("UC"):
            title = server.get_channel_name(ref) or ""
            channel = FollowedChannel(ref=ref, channel_id=ref, title=title)
        else:
            handle = ref if ref.startswith("@") else f"@{ref}"
            channel_id = server.get_handle(handle)
            if not channel_id:
                print(f"skip {ref}: unresolved handle (add it via POST /api/channels)")
                continue
            title = server.get_channel_name(channel_id) or ""
            channel = FollowedChannel(ref=handle, channel_id=channel_id, title=title)
        if server.add_channel(channel):
            added += 1
    print(f"channels: {added}/{len(refs)} imported")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-db", required=True, type=Path)
    parser.add_argument(
        "--client-db", type=Path, default=Path.home() / ".cache/ytui/meta.sqlite"
    )
    parser.add_argument(
        "--config", type=Path, default=Path.home() / ".config/ytui/config.toml"
    )
    args = parser.parse_args()

    started = time.time()
    server = Database(args.server_db)
    if args.client_db.exists():
        import_client_db(server, args.client_db)
    else:
        print(f"client DB not found, skipping: {args.client_db}")
    if args.config.exists():
        import_config_channels(server, args.config)
    else:
        print(f"config not found, skipping: {args.config}")
    server.close()
    print(f"done in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
