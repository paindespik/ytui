"""Config loading tests."""

from pathlib import Path

from ytui.config import Config, load_config


def test_load_missing_creates_default(tmp_path: Path):
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert path.exists()
    assert "[server]" in path.read_text()
    assert config.server.url == ""
    assert config.server.token == ""
    assert config.player.command == "mpv"
    assert config.player.audio_only is False
    assert config.ui.thumbnails is True
    assert config.auth.client_secret == ""


def test_created_default_file_is_reloadable(tmp_path: Path):
    path = tmp_path / "config.toml"
    load_config(path)  # creates the file
    config = load_config(path)  # parses it back
    assert config == Config()


def test_load_custom_values(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[server]
url = "https://ytui.example.com"
token = "secret"

[player]
command = "mpv"
audio_only = true

[ui]
thumbnails = false
"""
    )
    config = load_config(path)
    assert config.server.url == "https://ytui.example.com"
    assert config.server.token == "secret"
    assert config.player.audio_only is True
    assert config.ui.thumbnails is False


def test_auth_section_loads(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[auth]\nclient_secret = "/tmp/secret.json"\n')
    config = load_config(path)
    assert config.auth.client_secret == "/tmp/secret.json"


def test_legacy_sections_are_ignored(tmp_path: Path):
    """Pre-server configs with [feed]/[channels]/[live] still load."""
    path = tmp_path / "config.toml"
    path.write_text(
        """
[feed]
backend = "rss"

[channels]
list = ["@OnlyOne"]

[live]
notifications = true

[server]
url = "https://ytui.example.com"
"""
    )
    config = load_config(path)
    assert config.server.url == "https://ytui.example.com"


def test_partial_config_uses_defaults(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[server]\nurl = "https://x"\n')
    config = load_config(path)
    assert config.server.url == "https://x"
    assert config.player.format.startswith("bestvideo")
