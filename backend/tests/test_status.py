"""GET /api/status: diagnostics payload and auth."""

from __future__ import annotations


def test_status_payload(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "version",
        "uptime_seconds",
        "db_bytes",
        "history_rows",
        "channels",
        "feed_cached_channels",
        "feed_newest_age_seconds",
        "feed_oldest_age_seconds",
        "lives_active",
    ):
        assert key in body
    assert body["channels"] == 0
    assert body["history_rows"] == 0
    assert body["lives_active"] == 0
    assert body["uptime_seconds"] >= 0
    assert body["db_bytes"] > 0
    assert body["feed_newest_age_seconds"] is None


def test_status_counts_channels(client):
    client.post("/api/channels", json={"ref": "UCtest000000000000000000"})
    assert client.get("/api/status").json()["channels"] == 1


def test_status_requires_token(anon_client):
    assert anon_client.get("/api/status").status_code == 401


def test_status_per_platform(app, client):
    from ytui_server.models import FollowedChannel, Video

    db = app.state.db
    db.add_channel(
        FollowedChannel(ref="UCyt", channel_id="UCyt00000000000000000000", platform="youtube")
    )
    db.add_channel(
        FollowedChannel(ref="crowdbunker:Org", channel_id="Org", platform="crowdbunker")
    )
    db.add_channel(
        FollowedChannel(ref="odysee:@chan:1", channel_id="@chan:1", platform="odysee")
    )
    db.set_feed("UCyt00000000000000000000", [Video(video_id="v1", title="t")])
    db.set_feed("crowdbunker:Org", [Video(video_id="v2", title="t", platform="crowdbunker")])

    body = client.get("/api/status").json()
    assert body["channels"] == 3
    assert body["channels_by_platform"] == {
        "youtube": 1,
        "crowdbunker": 1,
        "odysee": 1,
    }
    ages = body["feed_newest_age_seconds_by_platform"]
    assert set(ages) == {"youtube", "crowdbunker"}
    assert all(age >= 0 for age in ages.values())
