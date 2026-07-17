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
    history_max_rows: int = Field(default=20000, validation_alias="YTUI_HISTORY_MAX_ROWS")
    log_level: str = Field(default="INFO", validation_alias="YTUI_LOG_LEVEL")
    sponsorblock_categories: str = Field(
        default="sponsor,selfpromo,interaction",
        validation_alias="YTUI_SPONSORBLOCK_CATEGORIES",
    )
    client_secret_path: Path | None = Field(
        default=None, validation_alias="YTUI_CLIENT_SECRET_PATH"
    )
