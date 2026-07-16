"""HTTP client for the ytui backend server (one method per API endpoint)."""

from __future__ import annotations

from urllib.parse import quote

import httpx

from .models import Comment, Video, VideoDetails


def _encode(video_id: str) -> str:
    """Path-encode a video id (Odysee ids contain ':')."""
    return quote(video_id, safe="")


# yt-dlp backed endpoints (details, streams) can take well over the default
# timeout, especially for Odysee; give them their own generous budget.
SLOW_TIMEOUT = 60.0


def resume_start(position: float, duration: float | None) -> float:
    """Position to resume from: 0 if duration is unknown or within 5% of the end."""
    if not duration or duration <= 0:
        return 0.0
    if position >= 0.95 * duration:
        return 0.0
    return position


class YtuiApiError(Exception):
    """API call failed: HTTP error status or the server is unreachable."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail if status_code == 0 else f"{status_code}: {detail}")


class FeedResult:
    """Feed videos plus non-fatal warnings (failed channels, offline fallback)."""

    def __init__(self, videos: list[Video], warnings: list[str] | None = None) -> None:
        self.videos = videos
        self.warnings = warnings or []


class FollowedChannel:
    def __init__(self, ref: str, channel_id: str, title: str = "", platform: str = "youtube"):
        self.ref = ref
        self.channel_id = channel_id
        self.title = title
        self.platform = platform


class LocalPlaylist:
    """A server-side local playlist: id, name, creation time and item count."""

    def __init__(self, playlist_id: int, name: str, created_at: float, item_count: int) -> None:
        self.id = playlist_id
        self.name = name
        self.created_at = created_at
        self.item_count = item_count


class PlaylistItem:
    def __init__(self, position: int, video: Video) -> None:
        self.position = position
        self.video = video


class YtuiClient:
    """Async client for the ytui backend REST API."""

    def __init__(self, base_url: str, token: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self.base_url or "http://unconfigured.invalid",
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(timeout),
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise YtuiApiError(0, f"Server timed out ({type(exc).__name__}): {exc}") from exc
        except httpx.HTTPError as exc:
            raise YtuiApiError(0, f"Server unreachable: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise YtuiApiError(response.status_code, str(detail))
        return response

    # -- health --

    async def health(self) -> dict:
        return (await self._request("GET", "/health")).json()

    # -- feed & discovery --

    async def feed(self, refresh: bool = False) -> FeedResult:
        data = (await self._request("GET", "/api/feed", params={"refresh": refresh})).json()
        return FeedResult(
            [Video.model_validate(v) for v in data["videos"]], data.get("warnings", [])
        )

    async def suggestions(self, refresh: bool = False) -> FeedResult:
        data = (
            await self._request("GET", "/api/suggestions", params={"refresh": refresh})
        ).json()
        return FeedResult(
            [Video.model_validate(v) for v in data["videos"]], data.get("warnings", [])
        )

    async def search(
        self, query: str, limit: int = 20, source: str = "youtube"
    ) -> list[Video]:
        data = (
            await self._request(
                "GET", "/api/search", params={"q": query, "limit": limit, "source": source}
            )
        ).json()
        return [Video.model_validate(v) for v in data["items"]]

    async def channel_videos(
        self, channel_id: str, platform: str = "youtube", limit: int = 50
    ) -> list[Video]:
        data = (
            await self._request(
                "GET",
                f"/api/channels/{_encode(channel_id)}/videos",
                params={"platform": platform, "limit": limit},
            )
        ).json()
        return [Video.model_validate(v) for v in data["items"]]

    async def playlist_videos(
        self, playlist_id: str, platform: str = "youtube", limit: int = 200
    ) -> list[Video]:
        data = (
            await self._request(
                "GET",
                f"/api/ytplaylists/{_encode(playlist_id)}/videos",
                params={"platform": platform, "limit": limit},
            )
        ).json()
        return [Video.model_validate(v) for v in data["items"]]

    async def video_details(self, video_id: str, platform: str = "youtube") -> VideoDetails:
        data = (
            await self._request(
                "GET",
                f"/api/videos/{_encode(video_id)}",
                params={"platform": platform},
                timeout=SLOW_TIMEOUT,
            )
        ).json()
        return VideoDetails.model_validate(data)

    async def video_comments(
        self, video_id: str, platform: str = "odysee", page: int = 1, page_size: int = 50
    ) -> tuple[list[Comment], int]:
        """(comments for one page, total comment count on the server)."""
        data = (
            await self._request(
                "GET",
                f"/api/videos/{_encode(video_id)}/comments",
                params={"platform": platform, "page": page, "page_size": page_size},
            )
        ).json()
        comments = [Comment.model_validate(c) for c in data["items"]]
        return comments, data.get("total", len(comments))

    async def video_streams(
        self,
        video_id: str,
        platform: str = "youtube",
        max_height: int = 1080,
        audio_only: bool = False,
    ) -> dict:
        return (
            await self._request(
                "GET",
                f"/api/videos/{_encode(video_id)}/streams",
                params={"platform": platform, "max_height": max_height, "audio_only": audio_only},
                timeout=SLOW_TIMEOUT,
            )
        ).json()

    # -- followed channels --

    async def channels(self) -> list[FollowedChannel]:
        data = (await self._request("GET", "/api/channels")).json()
        return [FollowedChannel(**c) for c in data]

    async def follow_channel(self, ref: str) -> FollowedChannel:
        data = (await self._request("POST", "/api/channels", json={"ref": ref})).json()
        return FollowedChannel(**data)

    async def unfollow_channel(self, channel_id: str) -> None:
        await self._request("DELETE", f"/api/channels/{_encode(channel_id)}")

    # -- watch history --

    async def history(self, limit: int = 200) -> list[Video]:
        data = (await self._request("GET", "/api/history", params={"limit": limit})).json()
        return [Video.model_validate(entry["video"]) for entry in data]

    async def record_watch(self, video: Video) -> None:
        await self._request("POST", "/api/history", json={"video": video.model_dump(mode="json")})

    async def watched_ids(self) -> set[str]:
        data = (await self._request("GET", "/api/history/watched-ids")).json()
        return set(data["ids"])

    async def save_position(
        self, video_id: str, position: float, duration: float | None
    ) -> None:
        await self._request(
            "PUT",
            f"/api/history/{_encode(video_id)}/position",
            json={"position": position, "duration": duration},
        )

    async def resume(self, video_id: str) -> tuple[float, float | None, str] | None:
        """(position, duration, playlist_id) for a watched video, or None."""
        try:
            data = (await self._request("GET", f"/api/history/{_encode(video_id)}/resume")).json()
        except YtuiApiError as exc:
            if exc.status_code == 404:
                return None
            raise
        return (data["position"], data.get("duration"), data.get("playlist_id", ""))

    async def remove_watch(self, video_id: str) -> None:
        await self._request("DELETE", f"/api/history/{_encode(video_id)}")

    # -- local playlists --

    async def playlists(self) -> list[LocalPlaylist]:
        data = (await self._request("GET", "/api/playlists")).json()
        return [LocalPlaylist(p["id"], p["name"], p["created_at"], p["count"]) for p in data]

    async def create_playlist(self, name: str) -> int | None:
        """Create a playlist. Returns its id, or None if the name is taken."""
        try:
            data = (await self._request("POST", "/api/playlists", json={"name": name})).json()
        except YtuiApiError as exc:
            if exc.status_code == 409:
                return None
            raise
        return data["id"]

    async def rename_playlist(self, playlist_id: int, name: str) -> bool:
        try:
            await self._request("PATCH", f"/api/playlists/{playlist_id}", json={"name": name})
        except YtuiApiError as exc:
            if exc.status_code == 409:
                return False
            raise
        return True

    async def delete_playlist(self, playlist_id: int) -> None:
        await self._request("DELETE", f"/api/playlists/{playlist_id}")

    async def playlist_items(self, playlist_id: int) -> list[PlaylistItem]:
        data = (await self._request("GET", f"/api/playlists/{playlist_id}/items")).json()
        return [
            PlaylistItem(item["position"], Video.model_validate(item["video"])) for item in data
        ]

    async def add_playlist_item(self, playlist_id: int, video: Video) -> bool:
        """Append an item to a playlist. Returns False if already present."""
        try:
            await self._request(
                "POST",
                f"/api/playlists/{playlist_id}/items",
                json={"video": video.model_dump(mode="json")},
            )
        except YtuiApiError as exc:
            if exc.status_code == 409:
                return False
            raise
        return True

    async def remove_playlist_item(self, playlist_id: int, position: int) -> None:
        await self._request("DELETE", f"/api/playlists/{playlist_id}/items/{position}")

    # -- YouTube account (like/comment) --

    async def like_video(self, video_id: str, platform: str = "youtube") -> None:
        await self._request(
            "POST", f"/api/videos/{_encode(video_id)}/like", params={"platform": platform}
        )

    async def comment_video(self, video_id: str, text: str, platform: str = "youtube") -> None:
        await self._request(
            "POST",
            f"/api/videos/{_encode(video_id)}/comment",
            params={"platform": platform},
            json={"text": text},
        )

    async def auth_status(self) -> bool:
        data = (await self._request("GET", "/api/auth/youtube/status")).json()
        return bool(data["authenticated"])

    async def push_youtube_token(self, token_json: str) -> None:
        await self._request(
            "POST",
            "/api/auth/youtube/token",
            content=token_json.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    # -- lives --

    async def lives(self) -> list[Video]:
        data = (await self._request("GET", "/api/lives")).json()
        return [Video.model_validate(entry["video"]) for entry in data]
