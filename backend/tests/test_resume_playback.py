"""Playlist playback + resume of partially watched videos.

Covers the contract every client (TUI, web, mobile) relies on to resume a
video where it was left: the resume point must survive re-watching, metadata
refreshes and concurrent heartbeats, and a local playlist must expose stable
positions so "play all" keeps its order.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import TEST_TOKEN, make_video
from fastapi.testclient import TestClient
from ytui_server.main import create_app
from ytui_server.settings import Settings

VIDEO_ID = "abc123def45"


def _record(client, video_id=VIDEO_ID, **kwargs):
    video = make_video(video_id, **kwargs)
    resp = client.post("/api/history", json={"video": video.model_dump(mode="json")})
    assert resp.status_code == 204
    return video


def _save(client, video_id, position, duration=600.0):
    resp = client.put(
        f"/api/history/{video_id}/position",
        json={"position": position, "duration": duration},
    )
    assert resp.status_code == 204
    return resp


def _resume(client, video_id):
    resp = client.get(f"/api/history/{video_id}/resume")
    assert resp.status_code == 200
    return resp.json()


def _entry(client, video_id):
    for entry in client.get("/api/history").json():
        if entry["video"]["video_id"] == video_id:
            return entry
    raise AssertionError(f"{video_id} not in history")


# --- resume point survives re-watching -------------------------------------


def test_record_watch_preserves_position(client):
    """Replaying a video must not reset its resume point.

    Every client POSTs /api/history when playback starts, *before* it GETs the
    resume position — a reset here would make resume impossible.
    """
    _record(client)
    _save(client, VIDEO_ID, 137.0, 600.0)
    _record(client)  # play it again: same call the player issues on load
    resume = _resume(client, VIDEO_ID)
    assert resume["position"] == 137.0
    assert resume["duration"] == 600.0


def test_record_watch_preserves_position_with_new_metadata(client):
    """A refreshed title/playlist_id/channel_id must not clear the position."""
    _record(client, playlist_id="PLoriginal", channel_id="UCorig00000000000000000")
    _save(client, VIDEO_ID, 42.0, 300.0)
    _record(
        client,
        title="Retitled upstream",
        playlist_id="PLother",
        channel_id="UCother0000000000000000",
    )
    resume = _resume(client, VIDEO_ID)
    assert resume["position"] == 42.0
    assert resume["duration"] == 300.0
    assert resume["playlist_id"] == "PLother"
    assert _entry(client, VIDEO_ID)["video"]["title"] == "Retitled upstream"


def test_record_watch_keeps_playlist_id_when_absent(client):
    """An empty playlist_id/channel_id never overwrites a known one."""
    _record(client, playlist_id="PLkeepme", channel_id="UCkeep000000000000000000")
    _save(client, VIDEO_ID, 10.0, 100.0)
    _record(client, playlist_id="", channel_id="")
    resume = _resume(client, VIDEO_ID)
    assert resume["playlist_id"] == "PLkeepme"
    assert resume["position"] == 10.0
    assert _entry(client, VIDEO_ID)["video"]["channel_id"] == "UCkeep000000000000000000"


def test_resume_exposes_position_duration_and_playlist(client):
    _record(client, playlist_id="PLbatch01")
    _save(client, VIDEO_ID, 12.5, 480.0)
    assert _resume(client, VIDEO_ID) == {
        "position": 12.5,
        "duration": 480.0,
        "playlist_id": "PLbatch01",
    }


def test_resume_defaults_before_any_position(client):
    _record(client)
    assert _resume(client, VIDEO_ID) == {
        "position": 0.0,
        "duration": None,
        "playlist_id": "",
    }


# --- position edge cases ----------------------------------------------------


def test_position_zero_resets_resume(client):
    """Restarting from the beginning is stored as-is (clients send 0)."""
    _record(client)
    _save(client, VIDEO_ID, 250.0, 600.0)
    _save(client, VIDEO_ID, 0.0, 600.0)
    assert _resume(client, VIDEO_ID)["position"] == 0.0


def test_position_without_duration(client):
    """duration is optional (a stream with no known length still heartbeats)."""
    _record(client)
    resp = client.put(f"/api/history/{VIDEO_ID}/position", json={"position": 33.0})
    assert resp.status_code == 204
    resume = _resume(client, VIDEO_ID)
    assert resume["position"] == 33.0
    assert resume["duration"] is None


def test_position_null_duration_overwrites_known_duration(client):
    """A later heartbeat without duration clears the stored one (observed)."""
    _record(client)
    _save(client, VIDEO_ID, 20.0, 600.0)
    resp = client.put(
        f"/api/history/{VIDEO_ID}/position", json={"position": 25.0, "duration": None}
    )
    assert resp.status_code == 204
    assert _resume(client, VIDEO_ID)["duration"] is None


def test_position_beyond_duration_is_stored_verbatim(client):
    """No server-side clamping: the client owns the 95 %-of-duration rule."""
    _record(client)
    _save(client, VIDEO_ID, 900.0, 600.0)
    resume = _resume(client, VIDEO_ID)
    assert resume["position"] == 900.0
    assert resume["duration"] == 600.0


def test_position_keeps_float_precision(client):
    _record(client)
    _save(client, VIDEO_ID, 123.456, 789.012)
    resume = _resume(client, VIDEO_ID)
    assert resume["position"] == pytest.approx(123.456)
    assert resume["duration"] == pytest.approx(789.012)


def test_position_accepts_integer_json(client):
    """JSON integers coerce to float rather than 422."""
    _record(client)
    resp = client.put(
        f"/api/history/{VIDEO_ID}/position", json={"position": 60, "duration": 600}
    )
    assert resp.status_code == 204
    assert _resume(client, VIDEO_ID)["position"] == 60.0


def test_position_rejects_non_numeric(client):
    _record(client)
    resp = client.put(
        f"/api/history/{VIDEO_ID}/position", json={"position": "halfway"}
    )
    assert resp.status_code == 422


def test_last_write_wins_on_sequential_heartbeats(client):
    _record(client)
    for seconds in (10.0, 20.0, 30.0, 40.0):
        _save(client, VIDEO_ID, seconds)
    assert _resume(client, VIDEO_ID)["position"] == 40.0


# --- unknown videos ---------------------------------------------------------


def test_position_unknown_video_is_404(client):
    resp = client.put(
        "/api/history/unknown00001/position", json={"position": 5.0, "duration": 10.0}
    )
    assert resp.status_code == 404


def test_resume_unknown_video_is_404(client):
    assert client.get("/api/history/unknown00001/resume").status_code == 404


def test_resume_404_after_history_removed(client):
    """Deleting a history entry drops its resume point."""
    _record(client)
    _save(client, VIDEO_ID, 99.0)
    assert client.delete(f"/api/history/{VIDEO_ID}").status_code == 204
    assert client.get(f"/api/history/{VIDEO_ID}/resume").status_code == 404
    # Re-watching starts from scratch.
    _record(client)
    assert _resume(client, VIDEO_ID)["position"] == 0.0


# --- history listing --------------------------------------------------------


def test_history_lists_position_per_entry(client):
    _record(client, "hist0000001")
    _record(client, "hist0000002")
    _save(client, "hist0000001", 11.0)
    _save(client, "hist0000002", 22.0)
    positions = {
        entry["video"]["video_id"]: entry["position"]
        for entry in client.get("/api/history").json()
    }
    assert positions == {"hist0000001": 11.0, "hist0000002": 22.0}


def test_history_exposes_duration_on_video(client):
    """duration feeds the progress bar drawn on history tiles."""
    _record(client)
    _save(client, VIDEO_ID, 30.0, 600.0)
    assert _entry(client, VIDEO_ID)["video"]["duration"] == 600


# --- local playlists: order + positions -------------------------------------


def _create_playlist(client, name="Ma playlist"):
    resp = client.post("/api/playlists", json={"name": name})
    assert resp.status_code == 201
    return resp.json()["id"]


def _add(client, playlist_id, video_id, **kwargs):
    video = make_video(video_id, **kwargs)
    resp = client.post(
        f"/api/playlists/{playlist_id}/items",
        json={"video": video.model_dump(mode="json")},
    )
    assert resp.status_code == 201
    return video


def _items(client, playlist_id):
    resp = client.get(f"/api/playlists/{playlist_id}/items")
    assert resp.status_code == 200
    return resp.json()


def test_playlist_items_keep_insertion_order(client):
    """"Play all" replays this exact order, so positions must be 0..n-1."""
    playlist_id = _create_playlist(client)
    ids = [f"plvid{i:06d}" for i in range(5)]
    for video_id in ids:
        _add(client, playlist_id, video_id)
    items = _items(client, playlist_id)
    assert [item["position"] for item in items] == [0, 1, 2, 3, 4]
    assert [item["video"]["video_id"] for item in items] == ids


def test_playlist_item_count_matches_items(client):
    playlist_id = _create_playlist(client)
    for i in range(3):
        _add(client, playlist_id, f"plvid{i:06d}")
    listed = {p["id"]: p for p in client.get("/api/playlists").json()}
    assert listed[playlist_id]["count"] == 3


def test_playlist_duplicate_item_is_409(client):
    playlist_id = _create_playlist(client)
    _add(client, playlist_id, "plvid000000")
    video = make_video("plvid000000")
    resp = client.post(
        f"/api/playlists/{playlist_id}/items",
        json={"video": video.model_dump(mode="json")},
    )
    assert resp.status_code == 409
    assert len(_items(client, playlist_id)) == 1


def test_playlist_remove_item_reindexes_positions(client):
    """Removing the middle item compacts positions, keeping order stable."""
    playlist_id = _create_playlist(client)
    ids = [f"plvid{i:06d}" for i in range(4)]
    for video_id in ids:
        _add(client, playlist_id, video_id)
    assert client.delete(f"/api/playlists/{playlist_id}/items/1").status_code == 204
    items = _items(client, playlist_id)
    assert [item["position"] for item in items] == [0, 1, 2]
    assert [item["video"]["video_id"] for item in items] == [ids[0], ids[2], ids[3]]


def test_playlist_append_after_removal_has_no_position_gap(client):
    """Positions stay contiguous after remove+append (no PK collision)."""
    playlist_id = _create_playlist(client)
    for i in range(3):
        _add(client, playlist_id, f"plvid{i:06d}")
    assert client.delete(f"/api/playlists/{playlist_id}/items/0").status_code == 204
    _add(client, playlist_id, "plvidnew001")
    items = _items(client, playlist_id)
    assert [item["position"] for item in items] == [0, 1, 2]
    assert items[-1]["video"]["video_id"] == "plvidnew001"


def test_playlist_remove_unknown_position_is_404(client):
    playlist_id = _create_playlist(client)
    _add(client, playlist_id, "plvid000000")
    assert client.delete(f"/api/playlists/{playlist_id}/items/9").status_code == 404
    assert len(_items(client, playlist_id)) == 1


def test_playlist_items_unknown_playlist_is_404(client):
    assert client.get("/api/playlists/4242/items").status_code == 404


def test_playlist_delete_removes_items(client):
    playlist_id = _create_playlist(client)
    _add(client, playlist_id, "plvid000000")
    assert client.delete(f"/api/playlists/{playlist_id}").status_code == 204
    assert client.get(f"/api/playlists/{playlist_id}/items").status_code == 404


def test_playlist_removal_does_not_touch_resume_points(client):
    """Dropping a playlist entry keeps the video's resume point in history."""
    playlist_id = _create_playlist(client)
    _add(client, playlist_id, "plvid000000")
    _record(client, "plvid000000")
    _save(client, "plvid000000", 77.0, 600.0)
    assert client.delete(f"/api/playlists/{playlist_id}/items/0").status_code == 204
    assert _resume(client, "plvid000000")["position"] == 77.0


# --- playlist playback: independent resume points ---------------------------


def test_each_playlist_video_resumes_independently(client):
    """Walking a playlist stores one resume point per video, not one global."""
    playlist_id = _create_playlist(client)
    positions = {"plvid000000": 30.0, "plvid000001": 90.0, "plvid000002": 0.0}
    for video_id in positions:
        _add(client, playlist_id, video_id)
        _record(client, video_id, playlist_id="PLsource01")
        _save(client, video_id, positions[video_id], 600.0)
    for video_id, expected in positions.items():
        resume = _resume(client, video_id)
        assert resume["position"] == expected
        assert resume["duration"] == 600.0
        assert resume["playlist_id"] == "PLsource01"


def test_replaying_playlist_resumes_each_video_where_it_stopped(client):
    """Second pass over the playlist: re-record must not wipe any position."""
    ids = ["plvid000000", "plvid000001", "plvid000002"]
    for i, video_id in enumerate(ids):
        _record(client, video_id)
        _save(client, video_id, 60.0 * (i + 1), 600.0)
    for video_id in ids:  # "play all" again
        _record(client, video_id)
    assert [_resume(client, v)["position"] for v in ids] == [60.0, 120.0, 180.0]


def test_advancing_in_playlist_does_not_disturb_other_entries(client):
    _record(client, "plvid000000")
    _record(client, "plvid000001")
    _save(client, "plvid000000", 45.0, 600.0)
    for seconds in (5.0, 15.0, 25.0):  # heartbeats on the next video
        _save(client, "plvid000001", seconds, 600.0)
    assert _resume(client, "plvid000000")["position"] == 45.0
    assert _resume(client, "plvid000001")["position"] == 25.0


# --- concurrency ------------------------------------------------------------


def test_concurrent_heartbeats_same_video(client):
    """Overlapping heartbeats (foreground + background) must not error."""
    _record(client)
    _save(client, VIDEO_ID, 1.0)

    def beat(seconds: int):
        return client.put(
            f"/api/history/{VIDEO_ID}/position",
            json={"position": float(seconds), "duration": 600.0},
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(beat, range(1, 61)))
    assert set(codes) == {204}
    resume = _resume(client, VIDEO_ID)
    assert resume["position"] in {float(s) for s in range(1, 61)}
    assert resume["duration"] == 600.0


def test_concurrent_heartbeats_distinct_videos(client):
    """Parallel writes across videos each land on their own row."""
    ids = [f"conc{i:07d}" for i in range(12)]
    for video_id in ids:
        _record(client, video_id)

    def beat(item):
        index, video_id = item
        return client.put(
            f"/api/history/{video_id}/position",
            json={"position": float(index) * 10, "duration": 600.0},
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(beat, enumerate(ids)))
    assert set(codes) == {204}
    for index, video_id in enumerate(ids):
        assert _resume(client, video_id)["position"] == float(index) * 10


def test_concurrent_record_and_position(client):
    """A record_watch racing a heartbeat never wipes the resume point."""
    _record(client)
    _save(client, VIDEO_ID, 200.0, 600.0)
    video = make_video(VIDEO_ID).model_dump(mode="json")

    def hammer(index: int):
        if index % 2:
            return client.post("/api/history", json={"video": video}).status_code
        return client.put(
            f"/api/history/{VIDEO_ID}/position",
            json={"position": 200.0, "duration": 600.0},
        ).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(hammer, range(40)))
    assert set(codes) == {204}
    assert _resume(client, VIDEO_ID)["position"] == 200.0


# --- history eviction -------------------------------------------------------


@pytest.fixture()
def capped_client(tmp_path):
    """Client whose history keeps only the 3 most recently watched rows."""
    settings = Settings(
        YTUI_API_TOKEN=TEST_TOKEN,
        YTUI_DATA_DIR=tmp_path / "data",
        YTUI_HISTORY_MAX_ROWS=3,
    )
    with TestClient(create_app(settings, start_pollers=False)) as client:
        client.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield client


def test_history_eviction_keeps_most_recent_rows(capped_client):
    ids = [f"evict{i:06d}" for i in range(6)]
    for video_id in ids:
        _record(capped_client, video_id)
    kept = {e["video"]["video_id"] for e in capped_client.get("/api/history").json()}
    assert kept == set(ids[-3:])


def test_history_eviction_drops_resume_of_evicted_video(capped_client):
    """Documented consequence: an evicted video loses its resume point."""
    _record(capped_client, "evict000000")
    _save(capped_client, "evict000000", 120.0, 600.0)
    for i in range(1, 4):
        _record(capped_client, f"evict{i:06d}")
    assert capped_client.get("/api/history/evict000000/resume").status_code == 404


def test_history_eviction_spares_recently_replayed_video(capped_client):
    """Re-watching refreshes watched_at, so eviction targets the others."""
    _record(capped_client, "evict000000")
    _save(capped_client, "evict000000", 120.0, 600.0)
    for i in range(1, 3):
        _record(capped_client, f"evict{i:06d}")
    _record(capped_client, "evict000000")  # replayed: back to the top
    _record(capped_client, "evict000003")  # pushes the oldest out
    assert _resume(capped_client, "evict000000")["position"] == 120.0
