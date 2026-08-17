"""CLI entry point: ytui | ytui play <url> | ytui search "query" | ytui import <url> |
ytui auth push."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

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

    ref = video_id_from_url(url)
    if ref is None:
        return  # playlists / unknown URLs: nothing to record
    video_id, platform = ref
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


def _cmd_import_playlist(config: Config, args) -> int:
    """Copy a whole upstream playlist into a local ytui playlist."""
    from .api_client import YtuiApiError

    client = _client(config)
    if client is None:
        return 1

    async def run() -> int:
        try:
            result = await client.import_playlist(
                args.url,
                platform=args.platform,
                name=args.name or "",
                target_id=args.into,
                limit=args.limit,
            )
        except YtuiApiError as exc:
            print(f"Import failed: {exc.detail}", file=sys.stderr)
            return 1
        finally:
            await client.close()
        skipped = f", {result.skipped} skipped" if result.skipped else ""
        print(
            f"Imported {result.added} videos into '{result.playlist.name}' "
            f"(id {result.playlist.id}, {result.playlist.item_count} total){skipped}."
        )
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


def _cmd_auth_cookies(config: Config, args) -> int:
    """Push (or drop) the browser's YouTube cookies for personalised suggestions."""
    from . import auth
    from .api_client import YtuiApiError

    client = _client(config)
    if client is None:
        return 1

    cookies_text: str | None = None
    from_browser = False
    if not args.clear:
        if args.file:
            try:
                cookies_text = Path(args.file).read_text(encoding="utf-8")
            except OSError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
        else:
            browser, _, profile = args.from_browser.partition(":")
            try:
                cookies_text = auth.export_browser_cookies(browser, profile or None)
            except auth.AuthError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            from_browser = True

    async def run() -> int:
        try:
            if cookies_text is None:
                await client.delete_youtube_cookies()
                print("YouTube cookies removed from the server.")
                return 0
            await client.push_youtube_cookies(cookies_text)
            _, cookies = await client.youtube_auth_status()
        except YtuiApiError as exc:
            print(f"Cookie upload failed: {exc}", file=sys.stderr)
            return 1
        finally:
            await client.close()
        if not cookies:
            print("Server did not keep the cookies.", file=sys.stderr)
            return 1
        print("Personalised suggestions enabled.")
        if from_browser:
            print(
                "Tip: a session you keep browsing in gets rotated by Google. Export from "
                "a private window and close it to keep the server session alive."
            )
        return 0

    return asyncio.run(run())


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

    import_parser = sub.add_parser(
        "import", help="Import a whole YouTube playlist into a local ytui playlist"
    )
    import_parser.add_argument("url", help="Playlist URL or id")
    import_parser.add_argument(
        "--name", help="Name of the playlist to create (default: the upstream title)"
    )
    import_parser.add_argument(
        "--into", type=int, metavar="ID", help="Append into this existing local playlist"
    )
    import_parser.add_argument(
        "--platform", default="youtube", help="youtube (default), bitchute, twitch, tiktok"
    )
    import_parser.add_argument(
        "-n", "--limit", type=int, default=500, help="Max videos to import (default 500)"
    )

    auth_parser = sub.add_parser("auth", help="YouTube account management")
    auth_sub = auth_parser.add_subparsers(dest="auth_command")
    auth_sub.add_parser(
        "push", help="Run the OAuth consent flow locally and push the token to the server"
    )
    cookies_parser = auth_sub.add_parser(
        "cookies", help="Push the browser's YouTube cookies (personalised suggestions)"
    )
    source = cookies_parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--from-browser",
        metavar="BROWSER[:PROFILE]",
        help="Read cookies from a local browser (firefox, zen, chromium, chrome, brave, edge…)",
    )
    source.add_argument(
        "--file", metavar="PATH", help="Netscape cookies.txt exported from the browser"
    )
    source.add_argument(
        "--clear", action="store_true", help="Remove the cookies stored on the server"
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

    if args.command == "import":
        return _cmd_import_playlist(config, args)

    if args.command == "auth":
        if args.auth_command == "push":
            return _cmd_auth_push(config)
        if args.auth_command == "cookies":
            return _cmd_auth_cookies(config, args)
        auth_parser.print_help()
        return 1

    from .app import YtuiApp

    YtuiApp(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
