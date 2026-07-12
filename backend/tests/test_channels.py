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
