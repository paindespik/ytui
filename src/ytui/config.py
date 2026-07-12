"""Configuration loading and validation (TOML + pydantic)."""

from __future__ import annotations

import tomllib
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir
from pydantic import BaseModel, Field

APP_NAME = "ytui"

DEFAULT_CONFIG_TOML = """\
# ytui configuration

[server]
# ytui backend server (feed, search, history, playlists, lives).
# Example: url = "https://ytui.example.com"
url = ""
token = ""

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


class ServerConfig(BaseModel):
    url: str = ""
    token: str = ""


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
    model_config = {"extra": "ignore"}

    server: ServerConfig = Field(default_factory=ServerConfig)
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


def set_option(section: str, key: str, value: object, path: Path | None = None) -> None:
    """Set a single [section].key option in config.toml, preserving comments."""
    import tomlkit

    path = path or config_path()
    doc = _load_doc(path)
    table = doc.setdefault(section, tomlkit.table())
    table[key] = value
    path.write_text(tomlkit.dumps(doc), encoding="utf-8")
