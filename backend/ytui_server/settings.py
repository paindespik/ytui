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
        default="https://eu.luminous.dev,https://lb-eu.cdn-perfprod.com",
        validation_alias="YTUI_TWITCH_PROXIES",
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
