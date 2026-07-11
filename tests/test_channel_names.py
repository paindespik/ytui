from ytui.cache import MetaCache

def test_channel_name_roundtrip(tmp_path):
    cache = MetaCache(tmp_path / "meta.sqlite")
    assert cache.get_channel_name("UCx") is None
    cache.set_channel_name("UCx", "My Channel")
    assert cache.get_channel_name("UCx") == "My Channel"
    cache.set_channel_name("UCx", "Renamed")
    assert cache.get_channel_name("UCx") == "Renamed"
    cache.close()


def test_channel_name_backfill_from_cached_feed(tmp_path):
    from ytui.models import Video

    cache = MetaCache(tmp_path / "meta.sqlite")
    videos = [Video(video_id="abc123", title="T", channel_title="Cool Channel", channel_id="UCy")]
    cache.set_feed("UCy", videos)
    # set_feed stores the name; wipe it to test the lazy backfill path too
    cache._conn.execute("DELETE FROM channel_names")
    cache._conn.commit()
    assert cache.get_channel_name("UCy") == "Cool Channel"
    cache.close()
