"""Watch history CRUD in MetaCache."""

from ytui.cache import MetaCache
from ytui.models import Video

V1 = Video(video_id="vid1", title="First", channel_title="Chan A")
V2 = Video(video_id="vid2", title="Second", channel_title="Chan B")
PL = Video(video_id="PLxyz", title="A playlist", kind="playlist")


def make_cache(tmp_path) -> MetaCache:
    return MetaCache(tmp_path / "meta.sqlite")


def test_record_and_list(tmp_path):
    cache = make_cache(tmp_path)
    cache.record_watch(V1)
    cache.record_watch(V2)
    history = cache.watch_history()
    assert [v.video_id for v in history] == ["vid2", "vid1"]  # newest first
    assert history[1].title == "First"
    assert cache.watched_ids() == {"vid1", "vid2"}


def test_rewatch_moves_to_top(tmp_path):
    cache = make_cache(tmp_path)
    cache.record_watch(V1)
    cache.record_watch(V2)
    import time

    time.sleep(0.01)
    cache.record_watch(V1)
    history = cache.watch_history()
    assert [v.video_id for v in history] == ["vid1", "vid2"]
    assert len(history) == 2  # no duplicate rows


def test_remove_watch(tmp_path):
    cache = make_cache(tmp_path)
    cache.record_watch(V1)
    cache.record_watch(V2)
    cache.remove_watch("vid1")
    assert cache.watched_ids() == {"vid2"}
    assert [v.video_id for v in cache.watch_history()] == ["vid2"]


def test_playlist_kind_preserved(tmp_path):
    cache = make_cache(tmp_path)
    cache.record_watch(PL)
    entry = cache.watch_history()[0]
    assert entry.kind == "playlist"
    assert entry.thumbnail_url == ""  # no i.ytimg URL for non-videos
