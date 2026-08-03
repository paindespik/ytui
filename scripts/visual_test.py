#!/usr/bin/env python3
"""Visual screenshot tester for the ytui TUI app.

Runs the app headless via Textual's pilot, navigates to each screen,
and exports screenshots as SVG + PNG for visual inspection.

Usage:
    .venv/bin/python scripts/visual_test.py [--output-dir screenshots]

Requires: cairosvg (pip install cairosvg)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── data models ──────────────────────────────────────────────────────────────

from ytui.api_client import FeedResult, FollowedChannel, LocalPlaylist, PlaylistItem
from ytui.config import Config
from ytui.models import Comment, Video, VideoDetails

VIDEO = Video(
    video_id="dQw4w9WgXcQ",
    title="Test Video Title",
    channel_title="Some Channel",
    channel_id="UC123",
    published=datetime(2024, 1, 1, tzinfo=timezone.utc),
)
PLAYLIST_VIDEO = Video(video_id="PLabc", title="A playlist", kind="playlist")
CHANNEL_VIDEO = Video(video_id="UCabc", title="A channel", kind="channel")
ODYSEE_VIDEO = Video(
    video_id="ma-video:abc123",
    title="An Odysee video",
    channel_title="@chan",
    channel_id="@chan:1",
    platform="odysee",
)

DETAILS = VideoDetails(
    video_id="dQw4w9WgXcQ",
    title="Test Video Title",
    description="A description with some text for testing.",
    channel_title="Some Channel",
    channel_id="UC123",
    view_count=123456,
    like_count=7890,
    duration=253,
)


# ── fake client ──────────────────────────────────────────────────────────────

class FakeClient:
    """In-memory stand-in for YtuiClient — covers all endpoints the screens call."""

    def __init__(self) -> None:
        self.base_url = "https://fake"
        self._watched: set[str] = set()
        self._history: list[Video] = []
        self._playlists: dict[int, tuple[str, list[Video]]] = {}
        self._next_playlist_id = 1

    async def close(self) -> None:
        pass

    # -- feed --
    async def feed(self, refresh: bool = False) -> FeedResult:
        return FeedResult([VIDEO], [])

    # -- search --
    async def search(self, query: str, limit: int = 20, source: str = "youtube") -> list[Video]:
        return [ODYSEE_VIDEO] if source == "odysee" else [VIDEO]

    async def search_odysee(self, query: str) -> list[Video]:
        return [ODYSEE_VIDEO]

    # -- channel / playlist content --
    async def channel_videos(self, channel_id: str, platform: str = "youtube", limit: int = 50, offset: int = 0) -> tuple[list[Video], bool]:
        return ([VIDEO], False)

    async def playlist_videos(self, playlist_id: str, platform: str = "youtube", limit: int = 50) -> list[Video]:
        return [VIDEO]

    # -- detail / comments --
    async def video_details(self, video_id: str, platform: str = "youtube") -> VideoDetails:
        return DETAILS

    async def video_comments(self, video_id: str, platform: str = "youtube") -> tuple[list[Comment], int]:
        return ([Comment(comment_id="c1", text="Nice one")], 1)

    # -- watched --
    async def watched_ids(self) -> set[str]:
        return self._watched

    async def mark_watched(self, video_id: str) -> None:
        self._watched.add(video_id)

    # -- history --
    async def history(self) -> list[Video]:
        return self._history

    async def add_to_history(self, video: Video) -> None:
        self._history.insert(0, video)

    async def remove_watch(self, video_id: str) -> None:
        self._history = [v for v in self._history if v.video_id != video_id]

    # -- channels --
    async def channels(self) -> list[FollowedChannel]:
        return []

    async def follow_channel(self, channel_id: str) -> None:
        pass

    async def unfollow_channel(self, channel_id: str) -> None:
        pass

    # -- playlists --
    async def playlists(self) -> list[LocalPlaylist]:
        result = []
        for pid, (name, items) in self._playlists.items():
            result.append(LocalPlaylist(
                playlist_id=pid, name=name, created_at=0.0, item_count=len(items)
            ))
        return result

    async def create_playlist(self, name: str) -> int | None:
        pid = self._next_playlist_id
        self._playlists[pid] = (name, [])
        self._next_playlist_id += 1
        return pid

    async def playlist_items(self, playlist_id: int) -> list[PlaylistItem]:
        _, items = self._playlists.get(playlist_id, ("", []))
        return [PlaylistItem(position=idx, video=v) for idx, v in enumerate(items)]

    async def remove_playlist_item(self, playlist_id: int, position: int) -> None:
        name, items = self._playlists.get(playlist_id, ("", []))
        if 0 <= position < len(items):
            items.pop(position)
            self._playlists[playlist_id] = (name, items)

    async def rename_playlist(self, playlist_id: int, name: str) -> bool:
        _, items = self._playlists.get(playlist_id, ("", []))
        self._playlists[playlist_id] = (name, items)
        return True

    # -- lives / resume / position --
    async def lives(self) -> list[Video]:
        return []

    async def resume(self, video_id: str) -> tuple[float, float] | None:
        return None

    async def save_position(self, video_id: str, position: float, duration: float | None) -> None:
        pass


# ── app bootstrap ────────────────────────────────────────────────────────────

def _make_app(tmp_path: Path):
    from ytui.app import YtuiApp

    config = Config()
    config.server.url = "https://fake"
    config.server.token = "t"
    app = YtuiApp(config)
    app.client = FakeClient()
    return app


# ── screenshot export ────────────────────────────────────────────────────────

def svg_to_png(svg_text: str, png_path: Path) -> None:
    """Convert SVG to PNG using cairosvg, with offline font fallback."""
    import cairosvg

    # Replace remote @font-face with a local fallback so cairosvg doesn't hang on DNS
    # Replace remote @font-face with local DejaVu Sans Mono (has box-drawing glyphs).
    cleaned = re.sub(
        r'@font-face\s*\{[^}]*url\([^)]*\)[^}]*\}',
        '@font-face { font-family: "Fallback"; src: local("DejaVu Sans Mono"); }',
        svg_text,
    )
    cleaned = cleaned.replace('"Fira Code"', '"Fallback"')

    cairosvg.svg2png(bytestring=cleaned.encode(), write_to=str(png_path))


def take_screenshot(app, output_dir: Path, label: str) -> None:
    """Export the current screen as SVG + PNG."""
    screen_name = app.screen.__class__.__name__
    svg = app.export_screenshot(title=f"ytui - {screen_name}")

    base = f"{label}_{screen_name.lower()}"
    svg_path = output_dir / f"{base}.svg"
    png_path = output_dir / f"{base}.png"

    svg_path.write_text(svg)
    try:
        svg_to_png(svg, png_path)
        print(f"  [{label}] {screen_name} -> {png_path}")
    except Exception as e:
        print(f"  [{label}] {screen_name} -> {svg_path} (PNG failed: {e})", file=sys.stderr)


# ── main ─────────────────────────────────────────────────────────────────────

async def run_screenshots(output_dir: Path) -> None:
    import tempfile

    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        os.environ["XDG_CACHE_HOME"] = str(tmp_path / "cache")
        os.environ["XDG_CONFIG_HOME"] = str(tmp_path / "config")

        app = _make_app(tmp_path)

        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause()

            print(f"\n{'=' * 60}")
            print("ytui Visual Screenshot Test")
            print(f"{'=' * 60}\n")

            # ── 1. Home ──
            print("[1/9] Home screen...")
            await pilot.pause()
            take_screenshot(app, output_dir, "01")

            # ── 2. Search ──
            print("[2/9] Search screen...")
            app.push_screen("search")
            await pilot.pause()
            take_screenshot(app, output_dir, "02")

            # ── 3. Channel ──
            print("[3/9] Channel screen...")
            app.pop_screen()
            await pilot.pause()
            from ytui.ui.screens.channel import ChannelScreen
            app.push_screen(ChannelScreen(CHANNEL_VIDEO))
            await pilot.pause()
            take_screenshot(app, output_dir, "03")

            # ── 4. Playlist ──
            print("[4/9] Playlist screen...")
            app.pop_screen()
            await pilot.pause()
            from ytui.ui.screens.playlist import PlaylistScreen
            app.push_screen(PlaylistScreen(PLAYLIST_VIDEO))
            await pilot.pause()
            take_screenshot(app, output_dir, "04")

            # ── 5. History ──
            print("[5/9] History screen...")
            app.pop_screen()
            await pilot.pause()
            app.push_screen("history")
            await pilot.pause()
            take_screenshot(app, output_dir, "05")

            # ── 6. Settings ──
            print("[6/9] Settings screen...")
            app.pop_screen()
            await pilot.pause()
            app.push_screen("settings")
            await pilot.pause()
            take_screenshot(app, output_dir, "06")

            # ── 7. Help ──
            print("[7/9] Help screen...")
            app.pop_screen()
            await pilot.pause()
            app.push_screen("help")
            await pilot.pause()
            take_screenshot(app, output_dir, "07")

            # ── 8. Detail ──
            print("[8/9] Detail screen...")
            app.pop_screen()
            await pilot.pause()
            from ytui.ui.screens.detail import VideoDetailScreen
            app.push_screen(VideoDetailScreen(VIDEO))
            await pilot.pause()
            take_screenshot(app, output_dir, "08")

            # ── 9. Local Playlists ──
            print("[9/9] Local Playlists screen...")
            app.pop_screen()
            await pilot.pause()
            app.push_screen("local_playlists")
            await pilot.pause()
            take_screenshot(app, output_dir, "09")

    print(f"\n{'=' * 60}")
    print(f"Done! Screenshots saved to: {output_dir}/")
    print(f"{'=' * 60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Take visual screenshots of the ytui TUI")
    parser.add_argument("--output-dir", default="screenshots", help="Output directory (default: screenshots)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    asyncio.run(run_screenshots(output_dir))


if __name__ == "__main__":
    main()
