"""Config loading tests."""

from pathlib import Path

from ytui.config import Config, load_config


def test_load_missing_creates_default(tmp_path: Path):
    path = tmp_path / "config.toml"
    config = load_config(path)
    assert path.exists()
    assert "[channels]" in path.read_text()
    assert config.feed.backend == "rss"
    assert config.channels.list == []
    assert config.player.command == "mpv"
    assert config.player.audio_only is False
    assert config.ui.thumbnails is True


def test_created_default_file_is_reloadable(tmp_path: Path):
    path = tmp_path / "config.toml"
    load_config(path)  # creates the file
    config = load_config(path)  # parses it back
    assert config == Config()


def test_load_custom_values(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[feed]
backend = "rss"

[channels]
list = ["UCXuqSBlHAE6Xw-yeJA0Tunw", "@SomeHandle"]

[player]
command = "mpv"
audio_only = true

[ui]
thumbnails = false
"""
    )
    config = load_config(path)
    assert config.channels.list == ["UCXuqSBlHAE6Xw-yeJA0Tunw", "@SomeHandle"]
    assert config.player.audio_only is True
    assert config.ui.thumbnails is False


def test_partial_config_uses_defaults(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text('[channels]\nlist = ["@OnlyOne"]\n')
    config = load_config(path)
    assert config.channels.list == ["@OnlyOne"]
    assert config.player.format.startswith("bestvideo")
