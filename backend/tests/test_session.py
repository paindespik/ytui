"""Session cookie auth: login/logout endpoints and cookie-based /api access."""

from __future__ import annotations

import pytest
from conftest import TEST_TOKEN
from fastapi.testclient import TestClient


@pytest.fixture()
def web_client(app):
    """Anonymous client over https so the Secure session cookie is replayed."""
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def test_login_sets_cookie(web_client):
    resp = web_client.post("/api/session", json={"token": TEST_TOKEN})
    assert resp.status_code == 204
    set_cookie = resp.headers["set-cookie"].lower()
    assert "ytui_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "secure" in set_cookie


def test_login_rejects_bad_token(web_client):
    resp = web_client.post("/api/session", json={"token": "wrong"})
    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers


def test_cookie_grants_api_access(web_client):
    assert web_client.get("/api/channels").status_code == 401
    web_client.post("/api/session", json={"token": TEST_TOKEN})
    resp = web_client.get("/api/channels")
    assert resp.status_code == 200
    assert resp.json() == []


def test_logout_revokes_access(web_client):
    web_client.post("/api/session", json={"token": TEST_TOKEN})
    assert web_client.get("/api/channels").status_code == 200
    assert web_client.delete("/api/session").status_code == 204
    assert web_client.get("/api/channels").status_code == 401


def test_session_status_reflects_cookie(web_client):
    assert web_client.get("/api/session").json() == {"authenticated": False}
    web_client.post("/api/session", json={"token": TEST_TOKEN})
    assert web_client.get("/api/session").json() == {"authenticated": True}


def test_bad_cookie_rejected(web_client):
    web_client.cookies.set("ytui_session", "forged")
    assert web_client.get("/api/channels").status_code == 401


def test_bearer_header_still_works(client):
    assert client.get("/api/channels").status_code == 200
