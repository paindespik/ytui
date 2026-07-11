"""CLI entry point: ytui | ytui play <url> | ytui search "query"."""

from __future__ import annotations

import argparse
import re
import sys

from .config import load_config

_YT_WATCH_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{6,})")
_YT_SHORT_RE = re.compile(r"youtu\.be/([A-Za-z0-9_-]{6,})")
_BITCHUTE_RE = re.compile(r"bitchute\.com/video/([^/?#]+)")


def _record_cli_watch(url: str) -> None:
    """Record a `ytui play <url>` in watch history, mirroring the TUI behaviour."""
    from .cache import MetaCache
    from .models import Video

    platform = "youtube"
    match = _YT_WATCH_RE.search(url) or _YT_SHORT_RE.search(url)
    if not match:
        match = _BITCHUTE_RE.search(url)
        if match:
            platform = "bitchute"
    if not match:
        return  # playlists / unknown URLs: nothing to record
    video_id = match.group(1)

    cache = MetaCache()
    try:
        # Reuse cached metadata (title/channel) when the video is in a feed.
        video = cache.find_cached_video(video_id)
        if video is None:
            video = Video(video_id=video_id, title=video_id, platform=platform)
        cache.record_watch(video)
    finally:
        cache.close()


def main(argv: list[str] | None = None) -> int:
    from . import __version__

    parser = argparse.ArgumentParser(
        prog="ytui", description="A terminal YouTube client (feed, search, mpv playback)."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    play_parser = sub.add_parser("play", help="Play a video or playlist URL in mpv")
    play_parser.add_argument("url", help="YouTube video or playlist URL")

    search_parser = sub.add_parser("search", help="Search YouTube and print results")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("-n", "--limit", type=int, default=10, help="Number of results")

    args = parser.parse_args(argv)
    config = load_config()

    if args.command == "play":
        from .player.mpv import PlayerError, play

        try:
            play(args.url, config.player)
            _record_cli_watch(args.url)
        except PlayerError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "search":
        from .sources.ytdlp_source import search_videos

        try:
            videos = search_videos(args.query, limit=args.limit)
        except Exception as exc:
            print(f"Search failed: {exc}", file=sys.stderr)
            return 1
        for video in videos:
            print(f"{video.url}  {video.channel_title}  {video.title}")
        return 0

    from .app import YtuiApp

    YtuiApp(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
