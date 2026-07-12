"""CLI entry point: ytui | ytui play <url> | ytui search "query" | ytui auth push."""

from __future__ import annotations

import argparse
import asyncio
import sys

from .config import Config, load_config


def _client(config: Config):
    from .api_client import YtuiClient

    if not config.server.url:
        print(
            "No server configured. Set [server] url and token in config.toml.",
            file=sys.stderr,
        )
        return None
    return YtuiClient(config.server.url, config.server.token)


def _record_cli_watch(config: Config, url: str) -> None:
    """Record a `ytui play <url>` in watch history, mirroring the TUI behaviour."""
    from .api_client import YtuiApiError
    from .models import Video, video_id_from_url

    video_id = video_id_from_url(url)
    if video_id is None:
        return  # playlists / unknown URLs: nothing to record
    if "bitchute.com/" in url:
        platform = "bitchute"
    elif "odysee.com/" in url:
        platform = "odysee"
    else:
        platform = "youtube"
    client = _client(config)
    if client is None:
        return

    async def record() -> None:
        try:
            await client.record_watch(Video(video_id=video_id, title=video_id, platform=platform))
        except YtuiApiError:
            pass  # offline: playback still works, history is best-effort
        finally:
            await client.close()

    asyncio.run(record())


def _cmd_search(config: Config, query: str, limit: int) -> int:
    from .api_client import YtuiApiError

    client = _client(config)
    if client is None:
        return 1

    async def run() -> int:
        try:
            videos = await client.search(query, limit=limit)
        except YtuiApiError as exc:
            print(f"Search failed: {exc}", file=sys.stderr)
            return 1
        finally:
            await client.close()
        for video in videos:
            print(f"{video.url}  {video.channel_title}  {video.title}")
        return 0

    return asyncio.run(run())


def _cmd_auth_push(config: Config) -> int:
    from . import auth
    from .api_client import YtuiApiError

    client = _client(config)
    if client is None:
        return 1
    try:
        token_json = auth.run_consent_flow(config)
    except auth.AuthError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    async def push() -> int:
        try:
            await client.push_youtube_token(token_json)
            authenticated = await client.auth_status()
        except YtuiApiError as exc:
            print(f"Token upload failed: {exc}", file=sys.stderr)
            return 1
        finally:
            await client.close()
        if not authenticated:
            print("Server did not accept the token.", file=sys.stderr)
            return 1
        print("YouTube account connected: like/comment are now available on all devices.")
        return 0

    return asyncio.run(push())


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

    auth_parser = sub.add_parser("auth", help="YouTube account management")
    auth_sub = auth_parser.add_subparsers(dest="auth_command")
    auth_sub.add_parser(
        "push", help="Run the OAuth consent flow locally and push the token to the server"
    )

    args = parser.parse_args(argv)
    config = load_config()

    if args.command == "play":
        from .player.mpv import PlayerError, play

        try:
            play(args.url, config.player)
            _record_cli_watch(config, args.url)
        except PlayerError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.command == "search":
        return _cmd_search(config, args.query, args.limit)

    if args.command == "auth":
        if args.auth_command == "push":
            return _cmd_auth_push(config)
        auth_parser.print_help()
        return 1

    from .app import YtuiApp

    YtuiApp(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
