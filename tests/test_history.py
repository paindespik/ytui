"""Watch history CRUD in MetaCache."""

import sqlite3

from ytui.cache import MetaCache, resume_start
from ytui.models import Video

V1 = Video(video_id="vid1", title="First", channel_title="Chan A", channel_id="UCaaa")
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


def test_find_cached_video(tmp_path):
    cache = make_cache(tmp_path)
    cache.set_feed("UCchan", [V1])
    found = cache.find_cached_video("vid1")
    assert found is not None and found.title == "First"
    assert cache.find_cached_video("nope") is None


def test_save_and_get_position(tmp_path):
    cache = make_cache(tmp_path)
    cache.record_watch(V1)
    cache.save_position("vid1", 123.5, 600.0)
    assert cache.get_resume("vid1") == (123.5, 600.0, "")


def test_save_position_unknown_video_noop(tmp_path):
    cache = make_cache(tmp_path)
    cache.save_position("ghost", 10.0, 100.0)
    assert cache.get_resume("ghost") is None


def test_rewatch_preserves_position(tmp_path):
    cache = make_cache(tmp_path)
    cache.record_watch(V1)
    cache.save_position("vid1", 120.0, 600.0)
    cache.record_watch(V1)
    row = cache.get_resume("vid1")
    assert row is not None and row[0] == 120.0 and row[1] == 600.0


def test_playlist_id_recorded_and_preserved(tmp_path):
    cache = make_cache(tmp_path)
    cache.record_watch(V1.model_copy(update={"playlist_id": "PLx"}))
    assert cache.get_resume("vid1")[2] == "PLx"
    cache.record_watch(V1)  # re-record without playlist context
    assert cache.get_resume("vid1")[2] == "PLx"
    assert cache.watch_history()[0].playlist_id == "PLx"


def test_channel_id_recorded_and_preserved(tmp_path):
    cache = make_cache(tmp_path)
    cache.record_watch(V1)
    assert cache.watch_history()[0].channel_id == "UCaaa"
    # Re-record without channel info (e.g. auto-advanced queue entry): preserved.
    cache.record_watch(Video(video_id="vid1", title="First"))
    assert cache.watch_history()[0].channel_id == "UCaaa"


def test_migration_adds_columns(tmp_path):
    db = tmp_path / "meta.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE watch_history ("
        "video_id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "channel_title TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'video', "
        "watched_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO watch_history (video_id, title, watched_at) VALUES ('old1', 'Old', 1.0)"
    )
    conn.commit()
    conn.close()

    cache = MetaCache(db)
    assert cache.get_resume("old1") == (0.0, None, "")
    cache.save_position("old1", 42.0, 100.0)
    assert cache.get_resume("old1") == (42.0, 100.0, "")
    assert cache.watch_history()[0].channel_id == ""


def test_resume_start_rule():
    assert resume_start(120.0, 600.0) == 120.0
    assert resume_start(571.0, 600.0) == 0.0  # >= 95% of the end
    assert resume_start(569.0, 600.0) == 569.0
    assert resume_start(120.0, None) == 0.0
    assert resume_start(120.0, 0.0) == 0.0


def test_cli_play_records_watch(tmp_path, monkeypatch):
    import ytui.__main__ as main_mod
    import ytui.cache as cache_mod

    db = tmp_path / "meta.sqlite"
    # _record_cli_watch instantiates MetaCache(); redirect it to a temp DB.
    orig_init = cache_mod.MetaCache.__init__
    monkeypatch.setattr(
        cache_mod.MetaCache,
        "__init__",
        lambda self, path=None: orig_init(self, db),
    )

    main_mod._record_cli_watch("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    main_mod._record_cli_watch("https://www.bitchute.com/video/AbC123xyz/")
    main_mod._record_cli_watch("https://www.youtube.com/playlist?list=PL1")  # ignored

    cache = cache_mod.MetaCache()
    assert cache.watched_ids() == {"dQw4w9WgXcQ", "AbC123xyz"}
