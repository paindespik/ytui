from ytui.cache import MetaCache

def test_channel_name_roundtrip(tmp_path):
    cache = MetaCache(tmp_path / "meta.sqlite")
    assert cache.get_channel_name("UCx") is None
    cache.set_channel_name("UCx", "My Channel")
    assert cache.get_channel_name("UCx") == "My Channel"
    cache.set_channel_name("UCx", "Renamed")
    assert cache.get_channel_name("UCx") == "Renamed"
    cache.close()
