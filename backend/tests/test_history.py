"""Watch history API."""

from __future__ import annotations

from conftest import make_video


def _record(client, video_id="abc123def45", **kwargs):
    video = make_video(video_id, **kwargs)
    resp = client.post("/api/history", json={"video": video.model_dump(mode="json")})
    assert resp.status_code == 204
    return video


def test_history_empty(client):
    assert client.get("/api/history").json() == []


def test_record_and_list(client):
    _record(client)
    entries = client.get("/api/history").json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["video"]["video_id"] == "abc123def45"
    assert entry["video"]["title"] == "Video abc123def45"
    assert entry["position"] == 0.0
    assert entry["watched_at"] > 0


def test_record_updates_watched_at(client):
    _record(client)
    _record(client, "other0000001")
    entries = client.get("/api/history").json()
    assert [e["video"]["video_id"] for e in entries] == ["other0000001", "abc123def45"]
    _record(client)  # re-watch first: moves to top
    entries = client.get("/api/history").json()
    assert entries[0]["video"]["video_id"] == "abc123def45"


def test_watched_ids(client):
    _record(client)
    _record(client, "other0000001")
    ids = client.get("/api/history/watched-ids").json()["ids"]
    assert set(ids) == {"abc123def45", "other0000001"}


def test_position_roundtrip(client):
    _record(client)
    resp = client.put(
        "/api/history/abc123def45/position", json={"position": 42.5, "duration": 600.0}
    )
    assert resp.status_code == 204
    resume = client.get("/api/history/abc123def45/resume").json()
    assert resume["position"] == 42.5
    assert resume["duration"] == 600.0


def test_position_unknown_video(client):
    resp = client.put("/api/history/nope/position", json={"position": 1.0, "duration": 2.0})
    assert resp.status_code == 404


def test_resume_unknown_video(client):
    assert client.get("/api/history/nope/resume").status_code == 404


def test_remove_watch(client):
    _record(client)
    assert client.delete("/api/history/abc123def45").status_code == 204
    assert client.get("/api/history").json() == []
    assert client.delete("/api/history/abc123def45").status_code == 404


def test_history_preserves_platform(client):
    _record(client, "bcvid", platform="bitchute")
    entry = client.get("/api/history").json()[0]
    assert entry["video"]["platform"] == "bitchute"
    assert entry["video"]["url"].startswith("https://www.bitchute.com/")


def test_history_odysee_platform(client):
    _record(client, "my-video:ab12", platform="odysee")
    entry = client.get("/api/history").json()[0]
    assert entry["video"]["platform"] == "odysee"
    assert entry["video"]["url"] == "https://odysee.com/my-video:ab12"
