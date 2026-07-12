"""Local playlists API."""

from __future__ import annotations

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
