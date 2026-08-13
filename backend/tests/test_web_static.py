"""Static SPA hosting: / serves index.html, API auth left untouched."""

from __future__ import annotations


def test_root_serves_index(anon_client):
    resp = anon_client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "ytui" in resp.text


def test_static_asset_served_with_revalidation(anon_client):
    resp = anon_client.get("/js/main.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_health_still_public(anon_client):
    assert anon_client.get("/health").status_code == 200


def test_api_still_requires_auth(anon_client):
    assert anon_client.get("/api/feed").status_code == 401


def test_front_end_tests_are_not_served(anon_client):
    """web/tests/ vit à côté du code qu'il teste, mais n'est pas du contenu web."""
    for path in ("/tests/harness.mjs", "/tests/queue.test.mjs", "/tests/README.md"):
        assert anon_client.get(path).status_code == 404, path


def test_app_shell_still_served(anon_client):
    resp = anon_client.get("/js/queue.js")
    assert resp.status_code == 200
    assert "PlayQueue" in resp.text
