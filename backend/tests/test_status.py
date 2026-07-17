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
