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
# Channels for the home feed. Accepts YouTube channel IDs (UC...), @handles,
# or BitChute channel slugs prefixed with "bitchute:" (the name in
# bitchute.com/channel/<slug>/ URLs).
# Example:
# list = [
#   "UCXuqSBlHAE6Xw-yeJA0Tunw",
#   "@LinusTechTips",
#   "bitchute:bitchute",
# ]
list = []

[player]
command = "mpv"
format = "bestvideo[height<=?1080]+bestaudio/best"
audio_only = false
# Directory for 'd' (download video)
download_dir = "~/Videos"

[ui]
thumbnails = true

[auth]
# Path to the OAuth2 client_secret.json (Google Cloud Console, type "Desktop app").
# Needed for the like ('L') and comment ('C') actions. Requires 'pip install ytui[auth]'.
# client_secret = "~/.config/ytui/client_secret.json"
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
    download_dir: str = "~/Videos"


class UIConfig(BaseModel):
    thumbnails: bool = True


class AuthConfig(BaseModel):
    # Path to the OAuth2 client_secret.json; empty = config_dir()/client_secret.json.
    client_secret: str = ""


class Config(BaseModel):
    feed: FeedConfig = Field(default_factory=FeedConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    player: PlayerConfig = Field(default_factory=PlayerConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)


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


def _load_doc(path: Path):
    import tomlkit

    if path.exists():
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return tomlkit.parse(DEFAULT_CONFIG_TOML)


def add_channel(channel_id: str, path: Path | None = None) -> bool:
    """Persist a channel into [channels].list in config.toml, preserving comments.

    Returns True if added, False if it was already present.
    """
    import tomlkit

    path = path or config_path()
    doc = _load_doc(path)
    channels = doc.setdefault("channels", tomlkit.table())
    entries = channels.setdefault("list", tomlkit.array())
    if channel_id in entries:
        return False
    entries.append(channel_id)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return True


def remove_channel(channel_id: str, path: Path | None = None) -> bool:
    """Remove a channel from [channels].list, preserving comments.

    Returns True if removed, False if it was not present.
    """
    import tomlkit

    path = path or config_path()
    doc = _load_doc(path)
    channels = doc.setdefault("channels", tomlkit.table())
    entries = channels.setdefault("list", tomlkit.array())
    if channel_id not in entries:
        return False
    entries.remove(channel_id)
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
    return True


def set_option(section: str, key: str, value: object, path: Path | None = None) -> None:
    """Set a single [section].key option in config.toml, preserving comments."""
    import tomlkit

    path = path or config_path()
    doc = _load_doc(path)
    table = doc.setdefault(section, tomlkit.table())
    table[key] = value
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
