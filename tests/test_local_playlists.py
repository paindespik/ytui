"""Local playlists CRUD and ordering in MetaCache."""

from ytui.cache import MetaCache
from ytui.models import Video

V1 = Video(video_id="vid1", title="One", channel_title="Chan A")
V2 = Video(video_id="vid2", title="Two", channel_title="Chan B")
V3 = Video(video_id="vid3", title="Three")
YT_PL = Video(video_id="PLxyz", title="A YouTube playlist", kind="playlist")


def make_cache(tmp_path) -> MetaCache:
    return MetaCache(tmp_path / "meta.sqlite")


def test_create_list_rename_delete(tmp_path):
    cache = make_cache(tmp_path)
    pid = cache.create_playlist("watch later")
    assert pid is not None
    assert cache.create_playlist("watch later") is None  # duplicate name
    playlists = cache.list_playlists()
    assert [p.name for p in playlists] == ["watch later"]
    assert playlists[0].item_count == 0

    assert cache.rename_playlist(pid, "later") is True
    assert [p.name for p in cache.list_playlists()] == ["later"]
    other = cache.create_playlist("music")
    assert cache.rename_playlist(other, "later") is False  # name taken

    cache.delete_playlist(pid)
    assert [p.name for p in cache.list_playlists()] == ["music"]


def test_items_ordering_and_dedup(tmp_path):
    cache = make_cache(tmp_path)
    pid = cache.create_playlist("mix")
    assert cache.add_playlist_item(pid, V1) is True
    assert cache.add_playlist_item(pid, V2) is True
    assert cache.add_playlist_item(pid, YT_PL) is True  # YouTube playlists allowed
    assert cache.add_playlist_item(pid, V1) is False  # dedup

    items = cache.playlist_items(pid)
    assert [v.video_id for v in items] == ["vid1", "vid2", "PLxyz"]
    assert items[2].kind == "playlist"
    assert cache.list_playlists()[0].item_count == 3


def test_remove_item_compacts_positions(tmp_path):
    cache = make_cache(tmp_path)
    pid = cache.create_playlist("mix")
    for v in (V1, V2, V3):
        cache.add_playlist_item(pid, v)
    cache.remove_playlist_item(pid, "vid2")
    assert [v.video_id for v in cache.playlist_items(pid)] == ["vid1", "vid3"]
    # New items land at the end after compaction.
    cache.add_playlist_item(pid, V2)
    assert [v.video_id for v in cache.playlist_items(pid)] == ["vid1", "vid3", "vid2"]


def test_delete_playlist_cascades_items(tmp_path):
    cache = make_cache(tmp_path)
    pid = cache.create_playlist("mix")
    cache.add_playlist_item(pid, V1)
    cache.delete_playlist(pid)
    pid2 = cache.create_playlist("mix")
    assert cache.playlist_items(pid2) == []
