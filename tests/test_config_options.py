"""set_option comment preservation in config.toml."""

from pathlib import Path

from ytui.config import load_config, set_option


def test_set_option(tmp_path: Path):
    path = tmp_path / "config.toml"
    load_config(path)
    set_option("player", "audio_only", True, path)
    set_option("server", "url", "https://ytui.example.com", path)
    config = load_config(path)
    assert config.player.audio_only is True
    assert config.server.url == "https://ytui.example.com"
    text = path.read_text()
    assert "# ytui configuration" in text  # comments preserved


def test_set_option_creates_file(tmp_path: Path):
    path = tmp_path / "sub" / "config.toml"
    set_option("ui", "thumbnails", False, path)
    assert load_config(path).ui.thumbnails is False
