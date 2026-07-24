"""Odysee (LBRY) service: Lighthouse search + SDK proxy resolve/claim_search,
Commentron comments (read-only). All calls are plain HTTP JSON — no yt-dlp here.

Video identity: video_id = "{name}:{claim_id}", channel_id = "@name:claim_id".
The canonical URL https://odysee.com/{video_id} is handled by yt-dlp's LBRY
extractor for details/streams/downloads.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import CommentOut, CommentsResponse, Video

LIGHTHOUSE_URL = "https://lighthouse.odysee.tv/search"
PROXY_URL = "https://api.na-backend.odysee.com/api/v1/proxy"
COMMENTS_URL = "https://comments.odysee.tv/api/v2"

TIMEOUT = httpx.Timeout(10.0)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) ytui-server/0.2"
_HEADERS = {"User-Agent": USER_AGENT}

log = logging.getLogger(__name__)


class OdyseeError(Exception):
    """An Odysee upstream call failed."""


def claim_id_from_video_id(video_id: str) -> str:
    """Extract the claim_id from a 'name:claim_id' video id (or return as-is)."""
    return video_id.rsplit(":", 1)[-1] if ":" in video_id else video_id


async def _proxy_call(client: httpx.AsyncClient, method: str, params: dict) -> dict:
    """JSON-RPC call against the Odysee SDK proxy."""
    resp = await client.post(
        PROXY_URL,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers=_HEADERS,
    )
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise OdyseeError(f"{method}: {body['error']}")
    return body.get("result") or {}


def _claim_to_video(claim: dict) -> Video | None:
    """Map a resolved/claim_search claim dict to a Video."""
    claim_id = claim.get("claim_id") or ""
    name = claim.get("name") or ""
    if not claim_id or not name:
        return None
    value: dict[str, Any] = claim.get("value") or {}
    value_type = claim.get("value_type") or ""
    if value_type == "channel" or name.startswith("@"):
        kind = "channel"
        video_id = f"{name}:{claim_id}"
        channel_id = video_id
        channel_title = value.get("title") or name
    else:
        kind = "video"
        video_id = f"{name}:{claim_id}"
        signing = claim.get("signing_channel") or {}
        ch_name = signing.get("name") or ""
        ch_claim = signing.get("claim_id") or ""
        channel_id = f"{ch_name}:{ch_claim}" if ch_name and ch_claim else ""
        channel_title = (signing.get("value") or {}).get("title") or ch_name
    published = None
    release = value.get("release_time") or claim.get("timestamp")
    if release:
        try:
            published = datetime.fromtimestamp(int(release), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            published = None
    duration = (value.get("video") or value.get("audio") or {}).get("duration")
    thumb = (value.get("thumbnail") or {}).get("url") or ""
    return Video(
        video_id=video_id,
        title=value.get("title") or name,
        channel_title=channel_title,
        channel_id=channel_id,
        published=published,
        duration=int(duration) if duration else None,
        thumbnail_url=thumb,
        kind=kind,
        platform="odysee",
    )


async def search(query: str, limit: int = 20) -> list[Video]:
    """Search Odysee: Lighthouse for hits, batch resolve for full metadata."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                LIGHTHOUSE_URL,
                params={"s": query, "size": limit, "nsfw": "false"},
                headers=_HEADERS,
            )
            resp.raise_for_status()
            hits = resp.json()
            if not isinstance(hits, list) or not hits:
                return []
            urls = [
                f"lbry://{h['name']}#{h['claimId']}"
                for h in hits
                if h.get("name") and h.get("claimId")
            ]
            if not urls:
                return []
            result = await _proxy_call(client, "resolve", {"urls": urls})
    except OdyseeError:
        raise
    except Exception as exc:
        log.warning("odysee search failed: %s", exc)
        raise OdyseeError(f"search failed: {exc}") from exc
    videos: list[Video] = []
    for url in urls:
        claim = result.get(url)
        if not isinstance(claim, dict) or "error" in claim:
            continue
        video = _claim_to_video(claim)
        if video:
            videos.append(video)
    return videos


async def channel_videos(channel_id: str, limit: int = 50) -> list[Video]:
    """Latest videos of a channel via claim_search (channel_id = '@name:claim').

    claim_search caps page_size at 50; pages are fetched until `limit` is reached.
    """
    claim_id = claim_id_from_video_id(channel_id)
    videos: list[Video] = []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            page = 1
            while len(videos) < limit:
                page_size = min(limit - len(videos), 50)
                result = await _proxy_call(
                    client,
                    "claim_search",
                    {
                        "channel_ids": [claim_id],
                        "claim_type": ["stream"],
                        "order_by": ["release_time"],
                        "page": page,
                        "page_size": page_size,
                        "has_source": True,
                        "no_totals": True,
                    },
                )
                items = result.get("items") or []
                for claim in items:
                    video = _claim_to_video(claim)
                    if video:
                        videos.append(video)
                if len(items) < page_size:
                    break
                page += 1
    except OdyseeError:
        raise
    except Exception as exc:
        log.warning("odysee channel listing failed for %s: %s", channel_id, exc)
        raise OdyseeError(f"channel listing failed: {exc}") from exc
    return videos[:limit]


async def resolve_channel(
    handle: str, client: httpx.AsyncClient | None = None
) -> tuple[str, str]:
    """Resolve a bare Odysee channel handle ('@name') to ('@name:claim_id', title).

    Odysee channel refs need the claim_id; users only know the handle. This
    resolves 'lbry://@name' via the SDK proxy to the canonical id and title.
    """
    name = handle if handle.startswith("@") else f"@{handle}"
    url = f"lbry://{name}"
    owns_client = client is None
    try:
        if client is None:
            client = httpx.AsyncClient(timeout=TIMEOUT)
        try:
            result = await _proxy_call(client, "resolve", {"urls": [url]})
        finally:
            if owns_client:
                await client.aclose()
    except OdyseeError:
        raise
    except Exception as exc:
        log.warning("odysee channel resolve failed for %s: %s", name, exc)
        raise OdyseeError(f"channel resolve failed: {exc}") from exc
    claim = result.get(url)
    if not isinstance(claim, dict) or "error" in claim:
        raise OdyseeError(f"Unknown Odysee channel {name!r}")
    claim_id = claim.get("claim_id") or ""
    resolved_name = claim.get("name") or name
    if claim.get("value_type") != "channel" or not claim_id:
        raise OdyseeError(f"{name!r} is not an Odysee channel")
    title = (claim.get("value") or {}).get("title") or resolved_name
    return f"{resolved_name}:{claim_id}", title


async def comments(claim_id: str, page: int = 1, page_size: int = 50) -> CommentsResponse:
    """List comments for a claim (public Commentron API) with reaction counts."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                f"{COMMENTS_URL}?m=comment.List",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "comment.List",
                    "params": {
                        "claim_id": claim_id,
                        "page": page,
                        "page_size": page_size,
                        "top_level": True,
                        "sort_by": 3,
                    },
                },
                headers=_HEADERS,
            )
            resp.raise_for_status()
            body = resp.json()
            if "error" in body:
                error = body["error"]
                message = error.get("message", "") if isinstance(error, dict) else str(error)
                if "disabled" in message.lower():
                    # comments disabled by the creator: a normal state, not a failure
                    return CommentsResponse(items=[], total=0)
                raise OdyseeError(f"comment.List: {body['error']}")
            result = body.get("result") or {}
            items = result.get("items") or []
            reactions: dict[str, dict] = {}
            comment_ids = [c["comment_id"] for c in items if c.get("comment_id")]
            if comment_ids:
                try:
                    resp = await client.post(
                        f"{COMMENTS_URL}?m=reaction.List",
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "reaction.List",
                            "params": {"comment_ids": ",".join(comment_ids)},
                        },
                        headers=_HEADERS,
                    )
                    resp.raise_for_status()
                    reactions = (resp.json().get("result") or {}).get(
                        "others_reactions"
                    ) or {}
                except Exception as exc:
                    log.warning("odysee reaction.List failed: %s", exc)
                    reactions = {}  # reactions are cosmetic; ignore failures
    except OdyseeError:
        raise
    except Exception as exc:
        log.warning("odysee comments fetch failed for %s: %s", claim_id, exc)
        raise OdyseeError(f"comments fetch failed: {exc}") from exc
    out: list[CommentOut] = []
    for c in items:
        comment_id = c.get("comment_id") or ""
        if not comment_id:
            continue
        reaction = reactions.get(comment_id) or {}
        out.append(
            CommentOut(
                comment_id=comment_id,
                text=c.get("comment") or "",
                channel_name=c.get("channel_name") or "",
                timestamp=c.get("timestamp"),
                replies=c.get("replies") or 0,
                likes=reaction.get("like") or 0,
                dislikes=reaction.get("dislike") or 0,
                is_pinned=bool(c.get("is_pinned")),
            )
        )
    return CommentsResponse(items=out, total=result.get("total_items") or len(out))
