"""On-demand read-only live-chat sessions for YouTube and Twitch.

Each `platform:video_id` gets one upstream session feeding a monotonic ring
buffer; clients poll `GET /api/lives/{video_id}/chat` with a cursor. Rooms are
spawned on first poll and reaped after `CHAT_ROOM_IDLE_SECONDS` with no poll.

YouTube uses the continuation flow (watch page -> live_chat GET -> get_live_chat
POST with the full INNERTUBE_CONTEXT). Twitch uses anonymous IRC-over-WebSocket
(justinfan nick, no token). Both are read-only; no message sending.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections import deque

import httpx
import websockets

from ..models import ChatMessage, ChatResponse
from ..routers.proxy import PROXY_UA

log = logging.getLogger(__name__)

CHAT_BUFFER_MAX = 200          # ring buffer size per room
CHAT_ROOM_IDLE_SECONDS = 45    # reap a room after this long with no client poll
YT_DEFAULT_TIMEOUT_MS = 5000   # fallback poll gap when upstream omits timeoutMs
TWITCH_WS_URL = "wss://irc-ws.chat.twitch.tv:443"
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")  # Twitch color tag; reject junk


def _extract_json_after(s: str, anchor: str, start: int = 0) -> str | None:
    """Return the JSON object literal that follows `anchor` in `s`, or None.

    Brace-balances while respecting string/escape state, so it stops at the
    matching close brace (ytInitialData / INNERTUBE_CONTEXT do not end at a
    predictable delimiter a naive regex could anchor on).
    """
    idx = s.find(anchor, start)
    if idx == -1:
        return None
    start = s.find("{", idx)
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def _find_json(s: str, anchor: str) -> dict | None:
    """First JSON object after any `anchor` occurrence that actually parses.

    Live pages sometimes carry an earlier spurious `ytInitialData` token whose
    following brace block is a single-quoted JS object (not JSON); walk the
    occurrences and return the first whose balanced ``{...}`` decodes.
    """
    pos = 0
    while True:
        idx = s.find(anchor, pos)
        if idx == -1:
            return None
        pos = idx + len(anchor)
        blob = _extract_json_after(s, anchor, idx)
        if not blob:
            continue
        try:
            return json.loads(blob)
        except ValueError:
            continue


def _parse_live_actions(lcc: dict) -> tuple[list[ChatMessage], str | None, int]:
    """Parse a liveChatContinuation dict into (messages, next_continuation, timeout_ms)."""
    messages: list[ChatMessage] = []
    for action in lcc.get("actions", []):
        renderer = (
            action.get("addChatItemAction", {})
            .get("item", {})
            .get("liveChatTextMessageRenderer")
        )
        if not renderer:
            continue  # paid / membership / system renderers ignored
        author = renderer.get("authorName", {}).get("simpleText", "")
        text = "".join(
            run.get("text") or (run.get("emoji", {}).get("shortcuts") or [""])[0]
            for run in renderer.get("message", {}).get("runs", [])
        )
        mid = renderer.get("id", "")
        ts = int(renderer.get("timestampUsec", "0") or "0") / 1_000_000
        messages.append(ChatMessage(id=mid, author=author, text=text, timestamp=ts))

    block = (lcc.get("continuations") or [{}])[0]
    data = (
        block.get("invalidationContinuationData")
        or block.get("timedContinuationData")
        or {}
    )
    cont = data.get("continuation")
    timeout = int(data.get("timeoutMs", YT_DEFAULT_TIMEOUT_MS))
    return messages, cont, timeout


def _parse_privmsg(line: str) -> ChatMessage | None:
    """Parse one IRCv3 PRIVMSG line into a ChatMessage, or None if not one."""
    tags: dict[str, str] = {}
    if line.startswith("@"):
        tagpart, _, line = line[1:].partition(" ")
        for kv in tagpart.split(";"):
            key, _, value = kv.partition("=")
            tags[key] = value

    marker = " PRIVMSG "
    pidx = line.find(marker)
    if pidx == -1:
        return None
    prefix = line[:pidx]
    rest = line[pidx + len(marker) :]
    tidx = rest.find(" :")
    if tidx == -1:
        return None
    text = rest[tidx + 2 :]

    nick = prefix[1:].split("!", 1)[0] if prefix.startswith(":") else ""
    author = tags.get("display-name") or nick
    if not author:
        return None
    color = tags.get("color") or None
    if color and not _HEX_COLOR_RE.match(color):
        color = None
    mid = tags.get("id") or f"{nick}-{tags.get('tmi-sent-ts', '')}"
    ts = int(tags.get("tmi-sent-ts") or "0") / 1000
    return ChatMessage(id=mid, author=author, text=text, color=color, timestamp=ts)


class _Room:
    """Per-session ring buffer + upstream task state."""

    def __init__(self) -> None:
        self.buffer: deque[tuple[int, ChatMessage]] = deque(maxlen=CHAT_BUFFER_MAX)
        self.seq: int = 0
        self.last_poll: float = time.monotonic()
        self.active: bool = True
        self.task: asyncio.Task | None = None

    def add(self, msg: ChatMessage) -> None:
        self.seq += 1
        self.buffer.append((self.seq, msg))

    @property
    def max_seq(self) -> int:
        return self.buffer[-1][0] if self.buffer else 0


class ChatManager:
    """Owns live-chat rooms keyed by ``platform:video_id``."""

    def __init__(self) -> None:
        self._rooms: dict[str, _Room] = {}

    async def poll(self, platform: str, video_id: str, cursor: int) -> ChatResponse:
        self._reap_idle()
        key = f"{platform}:{video_id}"
        room = self._rooms.get(key)
        if room is None:
            room = self._rooms[key] = _Room()
            room.task = asyncio.get_running_loop().create_task(
                self._run(platform, video_id, room)
            )
        room.last_poll = time.monotonic()
        if cursor < 0 or cursor > room.max_seq:  # stale cursor -> serve backlog
            cursor = 0
        messages = [m for seq, m in room.buffer if seq > cursor]
        return ChatResponse(messages=messages, cursor=room.max_seq, active=room.active)

    async def _run(self, platform: str, video_id: str, room: _Room) -> None:
        try:
            if platform == "youtube":
                await self._run_youtube(video_id, room)
            elif platform == "twitch":
                await self._run_twitch(video_id, room)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - upstream flakiness ends the session
            log.warning("chat session %s:%s failed: %s", platform, video_id, exc)
        room.active = False

    def _reap_idle(self) -> None:
        now = time.monotonic()
        stale = [
            key
            for key, room in self._rooms.items()
            if now - room.last_poll > CHAT_ROOM_IDLE_SECONDS
        ]
        for key in stale:
            room = self._rooms.pop(key)
            if room.task:
                room.task.cancel()

    async def shutdown(self) -> None:
        for room in self._rooms.values():
            if room.task:
                room.task.cancel()
        self._rooms.clear()

    async def _run_youtube(self, video_id: str, room: _Room) -> None:
        headers = {
            "User-Agent": PROXY_UA,
            "Accept-Language": "en-US,en",
        }
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0), headers=headers, follow_redirects=True
        ) as client:
            # Prime a real visitor session: SOCS bypasses the EU consent wall,
            # then the homepage GET mints VISITOR_INFO1_LIVE etc. Without these
            # session cookies the get_live_chat POST returns 200 with empty actions.
            client.cookies.set("SOCS", "CAI", domain=".youtube.com")
            await client.get("https://www.youtube.com/")
            reload_cont = api_key = ctx = None
            lcc = None
            for _ in range(4):  # transient watch pages omit the chat data; retry
                page = (await client.get(f"https://www.youtube.com/watch?v={video_id}")).text
                data = _find_json(page, "ytInitialData") or {}
                try:
                    reload_cont = data["contents"]["twoColumnWatchNextResults"][
                        "conversationBar"
                    ]["liveChatRenderer"]["continuations"][0]["reloadContinuationData"][
                        "continuation"
                    ]
                except (KeyError, IndexError, TypeError):
                    reload_cont = None
                api_key_match = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', page)
                ctx = _find_json(page, '"INNERTUBE_CONTEXT":')
                if reload_cont and api_key_match and ctx:
                    api_key = api_key_match.group(1)
                    first_page = (
                        await client.get(
                            f"https://www.youtube.com/live_chat?continuation={reload_cont}"
                        )
                    ).text
                    lcc = (
                        (_find_json(first_page, "ytInitialData") or {})
                        .get("continuationContents", {})
                        .get("liveChatContinuation")
                    )
                    if lcc:
                        break
                await asyncio.sleep(2)
            if not lcc:
                room.active = False  # not live / chat disabled / unavailable
                return
            visitor = ctx.get("client", {}).get("visitorData", "")
            msgs, cont, timeout = _parse_live_actions(lcc)
            for m in msgs:
                room.add(m)
            await asyncio.sleep(timeout / 1000)

            post_url = (
                "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"
                f"?key={api_key}&prettyPrint=false"
            )
            post_headers = {
                "Content-Type": "application/json",
                "X-Goog-Visitor-Id": visitor,
                "Origin": "https://www.youtube.com",
                "Referer": f"https://www.youtube.com/live_chat?continuation={reload_cont}",
            }
            while cont:
                resp = await client.post(
                    post_url,
                    headers=post_headers,
                    json={"context": ctx, "continuation": cont},
                )
                lcc = (
                    resp.json()
                    .get("continuationContents", {})
                    .get("liveChatContinuation")
                )
                if not lcc:
                    break  # chat ended
                msgs, cont, timeout = _parse_live_actions(lcc)
                for m in msgs:
                    room.add(m)
                await asyncio.sleep(timeout / 1000)

    async def _run_twitch(self, video_id: str, room: _Room) -> None:
        login = video_id.split(":", 1)[0]
        async with websockets.connect(TWITCH_WS_URL) as ws:
            await ws.send("CAP REQ :twitch.tv/tags")
            await ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
            await ws.send(f"JOIN #{login}")
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                for line in raw.split("\r\n"):
                    if not line:
                        continue
                    if line.startswith("PING"):
                        await ws.send("PONG :tmi.twitch.tv")
                    elif " PRIVMSG " in line:
                        msg = _parse_privmsg(line)
                        if msg:
                            room.add(msg)
