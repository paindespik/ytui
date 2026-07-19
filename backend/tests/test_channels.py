"""Followed channels CRUD."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx

CHANNEL_PAGE = (
    '<html><link rel="canonical" '
    'href="https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"></html>'
)


def test_add_uc_channel(client):
    resp = client.post("/api/channels", json={"ref": "UCtest000000000000000000"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["channel_id"] == "UCtest000000000000000000"
    assert body["platform"] == "youtube"


def test_add_channel_dedup(client):
    assert client.post("/api/channels", json={"ref": "UCtest000000000000000000"}).status_code == 201
    resp = client.post("/api/channels", json={"ref": "UCtest000000000000000000"})
    assert resp.status_code == 409


def test_add_bitchute_channel(client):
    resp = client.post("/api/channels", json={"ref": "bitchute:someslug"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["platform"] == "bitchute"
    assert body["channel_id"] == "someslug"


def test_add_odysee_channel(client):
    resp = client.post("/api/channels", json={"ref": "odysee:@somechan:1a"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["platform"] == "odysee"
    assert body["channel_id"] == "@somechan:1a"
    assert body["title"] == "@somechan"


def test_add_odysee_channel_url(client):
    resp = client.post("/api/channels", json={"ref": "https://odysee.com/@somechan:1a"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["platform"] == "odysee"
    assert body["channel_id"] == "@somechan:1a"
    assert body["ref"] == "odysee:@somechan:1a"


def test_add_odysee_channel_invalid(client):
    resp = client.post("/api/channels", json={"ref": "odysee:notachannel"})
    assert resp.status_code == 404


def test_add_handle_resolves(client):
    async def fake_get(url, **kwargs):
        return httpx.Response(200, text=CHANNEL_PAGE, request=httpx.Request("GET", str(url)))

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fake_get)):
        resp = client.post("/api/channels", json={"ref": "@SomeHandle"})
    assert resp.status_code == 201
    assert resp.json()["channel_id"] == "UCabcdefghijklmnopqrstuv"


def test_add_handle_unresolvable(client):
    async def fake_get(url, **kwargs):
        return httpx.Response(200, text="<html></html>", request=httpx.Request("GET", str(url)))

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fake_get)):
        resp = client.post("/api/channels", json={"ref": "@Nobody"})
    assert resp.status_code == 404


def test_add_handle_upstream_error(client):
    async def fake_get(url, **kwargs):
        raise httpx.ConnectError("down", request=httpx.Request("GET", str(url)))

    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=fake_get)):
        resp = client.post("/api/channels", json={"ref": "@Nobody"})
    assert resp.status_code == 502


def _get_status(status: int, text: str = "<html></html>"):
    async def fake(url, **kwargs):
        return httpx.Response(status, text=text, request=httpx.Request("GET", str(url)))

    return fake


def _gql_users(users):
    async def fake(url, **kwargs):
        payload = [{"data": {"users": users}}]
        return httpx.Response(200, json=payload, request=httpx.Request("POST", str(url)))

    return fake


def test_add_bare_ref_falls_back_to_twitch(client):
    """A bare name unknown to YouTube resolves as a Twitch login."""
    users = [{"login": "joueur_du_grenier", "displayName": "Joueur_du_Grenier", "stream": None}]
    with (
        patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_status(404))),
        patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=_gql_users(users))),
    ):
        resp = client.post("/api/channels", json={"ref": "Joueur_du_Grenier"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["ref"] == "twitch:joueur_du_grenier"
    assert body["platform"] == "twitch"
    assert body["title"] == "Joueur_du_Grenier"


def test_add_bare_ref_fallback_on_200_without_channel_id(client):
    """YouTube 200 pages with no channel id also trigger the fallback."""
    users = [{"login": "somestreamer", "displayName": "SomeStreamer", "stream": None}]
    with (
        patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_status(200))),
        patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=_gql_users(users))),
    ):
        resp = client.post("/api/channels", json={"ref": "SomeStreamer"})
    assert resp.status_code == 201
    assert resp.json()["ref"] == "twitch:somestreamer"


def test_add_bare_ref_unknown_everywhere(client):
    with (
        patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_status(404))),
        patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=_gql_users([None]))),
    ):
        resp = client.post("/api/channels", json={"ref": "no_such_name"})
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "no YouTube handle" in detail and "no Twitch channel" in detail


def test_add_explicit_handle_never_falls_back(client):
    """'@Nobody' states YouTube intent: no Twitch guess even if the login exists."""
    users = [{"login": "nobody", "displayName": "Nobody", "stream": None}]
    with (
        patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_status(404))),
        patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=_gql_users(users))),
    ):
        resp = client.post("/api/channels", json={"ref": "@Nobody"})
    assert resp.status_code == 404


def test_add_bare_ref_youtube_429_is_upstream_error(client):
    """Rate-limit/consent walls are not 'unknown handle': no fallback, 502."""
    with patch.object(httpx.AsyncClient, "get", AsyncMock(side_effect=_get_status(429))):
        resp = client.post("/api/channels", json={"ref": "somestreamer"})
    assert resp.status_code == 502


def test_add_empty_ref(client):
    resp = client.post("/api/channels", json={"ref": "  "})
    assert resp.status_code == 422


def test_list_and_delete_channel(client):
    client.post("/api/channels", json={"ref": "UCtest000000000000000000"})
    channels = client.get("/api/channels").json()
    assert len(channels) == 1
    resp = client.delete("/api/channels/UCtest000000000000000000")
    assert resp.status_code == 204
    assert client.get("/api/channels").json() == []


def test_delete_unknown_channel(client):
    resp = client.delete("/api/channels/UCunknown")
    assert resp.status_code == 404


def test_list_backfills_title_from_channel_names(app, client):
    """A channel added by bare UC id gets its title once the name is known."""
    client.post("/api/channels", json={"ref": "UCtest000000000000000000"})
    assert client.get("/api/channels").json()[0]["title"] == ""
    app.state.db.set_channel_name("UCtest000000000000000000", "Test Channel")
    assert client.get("/api/channels").json()[0]["title"] == "Test Channel"
