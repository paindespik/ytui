"""remove_channel / set_option comment preservation in config.toml."""

from pathlib import Path

from ytui.config import add_channel, load_config, remove_channel, set_option


def test_remove_channel(tmp_path: Path):
    path = tmp_path / "config.toml"
    add_channel("UC111", path)
    add_channel("UC222", path)
    assert remove_channel("UC111", path) is True
    assert load_config(path).channels.list == ["UC222"]
    assert remove_channel("UC111", path) is False  # already gone


def test_remove_channel_preserves_comments(tmp_path: Path):
    path = tmp_path / "config.toml"
    load_config(path)  # write commented default file
    add_channel("UC111", path)
    remove_channel("UC111", path)
    text = path.read_text()
    assert "# ytui configuration" in text
    assert "UC111" not in text


def test_set_option(tmp_path: Path):
    path = tmp_path / "config.toml"
    load_config(path)
    set_option("player", "audio_only", True, path)
    set_option("feed", "backend", "api", path)
    config = load_config(path)
    assert config.player.audio_only is True
    assert config.feed.backend == "api"
    text = path.read_text()
    assert "# ytui configuration" in text  # comments preserved


def test_set_option_creates_file(tmp_path: Path):
    path = tmp_path / "sub" / "config.toml"
    set_option("ui", "thumbnails", False, path)
    assert load_config(path).ui.thumbnails is False
