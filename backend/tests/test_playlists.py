"""Local playlists API."""

from __future__ import annotations

from unittest.mock import patch

from conftest import make_video


def _create(client, name="Watch later") -> int:
    resp = client.post("/api/playlists", json={"name": name})
    assert resp.status_code == 201
    return resp.json()["id"]


def _add_item(client, playlist_id: int, video_id="abc123def45", **kwargs):
    video = make_video(video_id, **kwargs)
    return client.post(
        f"/api/playlists/{playlist_id}/items", json={"video": video.model_dump(mode="json")}
    )


def test_create_and_list(client):
    playlist_id = _create(client)
    playlists = client.get("/api/playlists").json()
    assert len(playlists) == 1
    assert playlists[0] == {
        "id": playlist_id,
        "name": "Watch later",
        "created_at": playlists[0]["created_at"],
        "count": 0,
    }


def test_create_duplicate_name(client):
    _create(client)
    resp = client.post("/api/playlists", json={"name": "Watch later"})
    assert resp.status_code == 409


def test_create_empty_name(client):
    resp = client.post("/api/playlists", json={"name": "  "})
    assert resp.status_code == 422


def test_rename(client):
    playlist_id = _create(client)
    resp = client.patch(f"/api/playlists/{playlist_id}", json={"name": "Later"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Later"


def test_rename_conflict(client):
    _create(client, "A")
    playlist_id = _create(client, "B")
    resp = client.patch(f"/api/playlists/{playlist_id}", json={"name": "A"})
    assert resp.status_code == 409


def test_rename_unknown(client):
    assert client.patch("/api/playlists/999", json={"name": "X"}).status_code == 404


def test_delete(client):
    playlist_id = _create(client)
    assert client.delete(f"/api/playlists/{playlist_id}").status_code == 204
    assert client.get("/api/playlists").json() == []
    assert client.delete(f"/api/playlists/{playlist_id}").status_code == 404


def test_add_and_list_items(client):
    playlist_id = _create(client)
    assert _add_item(client, playlist_id).status_code == 201
    assert _add_item(client, playlist_id, "other0000001").status_code == 201
    items = client.get(f"/api/playlists/{playlist_id}/items").json()
    assert [i["position"] for i in items] == [0, 1]
    assert items[0]["video"]["video_id"] == "abc123def45"
    # count reflected in the listing
    assert client.get("/api/playlists").json()[0]["count"] == 2


def test_add_duplicate_item(client):
    playlist_id = _create(client)
    _add_item(client, playlist_id)
    assert _add_item(client, playlist_id).status_code == 409


def test_add_item_unknown_playlist(client):
    assert _add_item(client, 999).status_code == 404


def test_remove_item_compacts_positions(client):
    playlist_id = _create(client)
    _add_item(client, playlist_id, "vid000000001")
    _add_item(client, playlist_id, "vid000000002")
    _add_item(client, playlist_id, "vid000000003")
    resp = client.delete(f"/api/playlists/{playlist_id}/items/0")
    assert resp.status_code == 204
    items = client.get(f"/api/playlists/{playlist_id}/items").json()
    assert [(i["position"], i["video"]["video_id"]) for i in items] == [
        (0, "vid000000002"),
        (1, "vid000000003"),
    ]


def test_remove_item_unknown_position(client):
    playlist_id = _create(client)
    assert client.delete(f"/api/playlists/{playlist_id}/items/5").status_code == 404


# ─── import of a whole upstream playlist ───


class _FakeYDL:
    def __init__(self, info):
        self._info = info

    def __call__(self, opts):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        if isinstance(self._info, Exception):
            raise self._info
        return self._info


def _patch_ydl(info):
    import yt_dlp

    return patch.object(yt_dlp, "YoutubeDL", _FakeYDL(info))


_ENTRIES = [
    {"id": "vid000000001", "title": "V1", "channel": "Chan"},
    {"id": "vid000000002", "title": "V2", "channel": "Chan"},
]


def test_import_creates_playlist_named_after_source(client):
    with _patch_ydl({"entries": _ENTRIES, "title": "My List"}):
        resp = client.post("/api/playlists/import", json={"source": "PLxyz"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["added"] == 2
    assert body["skipped"] == 0
    assert body["source_title"] == "My List"
    assert body["playlist"]["name"] == "My List"
    assert body["playlist"]["count"] == 2
    items = client.get(f"/api/playlists/{body['playlist']['id']}/items").json()
    assert [i["video"]["video_id"] for i in items] == ["vid000000001", "vid000000002"]


def test_import_accepts_a_url_and_a_custom_name(client):
    with _patch_ydl({"entries": _ENTRIES, "title": "My List"}):
        resp = client.post(
            "/api/playlists/import",
            json={
                "source": "https://www.youtube.com/playlist?list=PLxyz",
                "name": "Mine",
            },
        )
    assert resp.status_code == 201
    assert resp.json()["playlist"]["name"] == "Mine"


def test_import_into_existing_playlist_skips_duplicates(client):
    playlist_id = _create(client, "Target")
    _add_item(client, playlist_id, "vid000000001")
    with _patch_ydl({"entries": _ENTRIES, "title": "My List"}):
        resp = client.post(
            "/api/playlists/import", json={"source": "PLxyz", "target_id": playlist_id}
        )
    assert resp.status_code == 201
    body = resp.json()
    assert (body["added"], body["skipped"]) == (1, 1)
    items = client.get(f"/api/playlists/{playlist_id}/items").json()
    assert [i["position"] for i in items] == [0, 1]


def test_import_name_conflict(client):
    _create(client, "My List")
    with _patch_ydl({"entries": _ENTRIES, "title": "My List"}):
        resp = client.post("/api/playlists/import", json={"source": "PLxyz"})
    assert resp.status_code == 409


def test_import_unknown_target(client):
    with _patch_ydl({"entries": _ENTRIES, "title": "My List"}):
        resp = client.post("/api/playlists/import", json={"source": "PLxyz", "target_id": 999})
    assert resp.status_code == 404


def test_import_upstream_error(client):
    with _patch_ydl(RuntimeError("boom")):
        resp = client.post("/api/playlists/import", json={"source": "PLxyz"})
    assert resp.status_code == 502


def test_import_odysee_unsupported(client):
    resp = client.post("/api/playlists/import", json={"source": "PL", "platform": "odysee"})
    assert resp.status_code == 400


def test_import_empty_source(client):
    assert client.post("/api/playlists/import", json={"source": "  "}).status_code == 422


def test_import_twitch_unsupported(client):
    resp = client.post("/api/playlists/import", json={"source": "PL", "platform": "twitch"})
    assert resp.status_code == 400
    assert "Twitch playlists" in resp.json()["detail"]


def test_twitch_playlist_listing_unsupported(client):
    resp = client.get("/api/ytplaylists/whatever/videos", params={"platform": "twitch"})
    assert resp.status_code == 400
    assert "Twitch playlists" in resp.json()["detail"]
