"""Server settings from environment variables (YTUI_ prefix)."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    api_token: str = Field(
        min_length=1, validation_alias=AliasChoices("YTUI_API_TOKEN", "YTUI_TOKEN")
    )
    data_dir: Path = Field(default=Path("/data"), validation_alias="YTUI_DATA_DIR")
    feed_ttl_minutes: int = Field(default=15, validation_alias="YTUI_FEED_TTL_MINUTES")
    suggestions_ttl_minutes: int = Field(
        default=30, validation_alias="YTUI_SUGGESTIONS_TTL_MINUTES"
    )
    music_ttl_minutes: int = Field(default=30, validation_alias="YTUI_MUSIC_TTL_MINUTES")
    # Brand channel of the cookie account to act as for the YouTube home and
    # YouTube Music (@handle or page id). Empty: whatever identity the session
    # is on — the Google account's primary channel unless switched in the
    # browser that holds it.
    youtube_channel: str = Field(default="", validation_alias="YTUI_YOUTUBE_CHANNEL")
    # Periodic authenticated ping that keeps the pushed cookie session alive
    # (yt-dlp rewrites the rotated cookies on each use). 0 disables.
    suggestions_keepalive_hours: int = Field(
        default=6, validation_alias="YTUI_SUGGESTIONS_KEEPALIVE_HOURS"
    )
    live_check_minutes: int = Field(default=5, validation_alias="YTUI_LIVE_CHECK_MINUTES")
    # Twitch lives are one batched GQL request for all channels, so they can
    # be polled much faster than the per-channel YouTube scrape.
    twitch_check_seconds: int = Field(
        default=60, validation_alias="YTUI_TWITCH_CHECK_SECONDS"
    )
    # TikTok liveness is one unsigned GET per followed channel
    # (api-live/user/room), light enough for the same cadence as Twitch.
    tiktok_check_seconds: int = Field(
        default=60, validation_alias="YTUI_TIKTOK_CHECK_SECONDS"
    )
    # TTV.LOL-compatible playlist proxies for ad-free Twitch streams, tried in
    # order with fallback to the direct (ad-fed) stream. Empty disables.
    twitch_proxies: str = Field(
        # lb-eu.cdn-perfprod.com dropped 2026-10-02: Cloudflare answers, the
        # origin hangs until the 10 s timeout.
        default="https://eu.luminous.dev,https://eu2.luminous.dev,https://lb-as.cdn-perfprod.com",
        validation_alias="YTUI_TWITCH_PROXIES",
    )
    # Extra domains (comma-separated, subdomains included) allowed through
    # /api/proxy on top of the built-in media-CDN allowlist.
    proxy_allowed_domains: str = Field(
        default="", validation_alias="YTUI_PROXY_ALLOWED_DOMAINS"
    )
    history_max_rows: int = Field(default=20000, validation_alias="YTUI_HISTORY_MAX_ROWS")
    log_level: str = Field(default="INFO", validation_alias="YTUI_LOG_LEVEL")
    sponsorblock_categories: str = Field(
        default="sponsor,selfpromo,interaction",
        validation_alias="YTUI_SPONSORBLOCK_CATEGORIES",
    )
    client_secret_path: Path | None = Field(
        default=None, validation_alias="YTUI_CLIENT_SECRET_PATH"
    )
