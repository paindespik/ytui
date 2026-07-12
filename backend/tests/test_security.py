"""Token auth: /health public, /api/* requires the bearer token."""

from __future__ import annotations

from conftest import TEST_TOKEN


def test_health_public(anon_client):
    resp = anon_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_api_requires_token(anon_client):
    resp = anon_client.get("/api/channels")
    assert resp.status_code == 401


def test_api_rejects_bad_token(anon_client):
    resp = anon_client.get(
        "/api/channels", headers={"Authorization": "Bearer wrong-token"}
    )
    assert resp.status_code == 401


def test_api_rejects_non_bearer_scheme(anon_client):
    resp = anon_client.get(
        "/api/channels", headers={"Authorization": f"Basic {TEST_TOKEN}"}
    )
    assert resp.status_code == 401


def test_api_accepts_valid_token(client):
    resp = client.get("/api/channels")
    assert resp.status_code == 200
    assert resp.json() == []
