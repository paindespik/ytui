"""Configuration loading and validation (TOML + pydantic)."""

from __future__ import annotations

import tomllib
import typing
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir
from pydantic import BaseModel, Field

APP_NAME = "ytui"

DEFAULT_CONFIG_TOML = """\
# ytui configuration

[feed]
# Feed backend: "rss" (default, no account needed)
backend = "rss"

[channels]
# Channels for the home feed. Accepts channel IDs (UC...) or @handles.
# Example:
# list = [
#   "UCXuqSBlHAE6Xw-yeJA0Tunw",
#   "@LinusTechTips",
# ]
list = []

[player]
command = "mpv"
format = "bestvideo[height<=?1080]+bestaudio/best"
audio_only = false

[ui]
thumbnails = true
"""


class FeedConfig(BaseModel):
    backend: str = "rss"


class ChannelsConfig(BaseModel):
    # Named "list" to match the TOML key; annotate via typing.List to avoid
    # the field name shadowing the builtin during annotation evaluation.
    list: typing.List[str] = Field(default_factory=lambda: [])  # noqa: UP006


class PlayerConfig(BaseModel):
    command: str = "mpv"
    format: str = "bestvideo[height<=?1080]+bestaudio/best"
    audio_only: bool = False


class UIConfig(BaseModel):
    thumbnails: bool = True


class Config(BaseModel):
    feed: FeedConfig = Field(default_factory=FeedConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    player: PlayerConfig = Field(default_factory=PlayerConfig)
    ui: UIConfig = Field(default_factory=UIConfig)


def config_dir() -> Path:
    return Path(user_config_dir(APP_NAME))


def config_path() -> Path:
    return config_dir() / "config.toml"


def cache_dir() -> Path:
    return Path(user_cache_dir(APP_NAME))


def load_config(path: Path | None = None) -> Config:
    """Load config from TOML, creating a commented default file on first run."""
    path = path or config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        return Config()
    with path.open("rb") as f:
        data = tomllib.load(f)
    return Config.model_validate(data)
