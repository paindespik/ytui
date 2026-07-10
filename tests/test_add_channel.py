"""Tests for persisting channels into config.toml."""

from pathlib import Path

from ytui.config import add_channel, load_config


def test_add_channel_to_existing_config(tmp_path: Path):
    path = tmp_path / "config.toml"
    load_config(path)  # create defaults
    assert add_channel("UCXuqSBlHAE6Xw-yeJA0Tunw", path) is True
    config = load_config(path)
    assert config.channels.list == ["UCXuqSBlHAE6Xw-yeJA0Tunw"]


def test_add_channel_creates_config(tmp_path: Path):
    path = tmp_path / "sub" / "config.toml"
    assert add_channel("UC123", path) is True
    assert path.exists()
    assert load_config(path).channels.list == ["UC123"]


def test_add_channel_deduplicates(tmp_path: Path):
    path = tmp_path / "config.toml"
    assert add_channel("UC123", path) is True
    assert add_channel("UC123", path) is False
    assert load_config(path).channels.list == ["UC123"]


def test_add_channel_preserves_comments(tmp_path: Path):
    path = tmp_path / "config.toml"
    load_config(path)  # write commented default file
    add_channel("UC123", path)
    text = path.read_text()
    assert "# ytui configuration" in text
    assert '"UC123"' in text
