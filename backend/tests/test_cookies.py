"""CookieStore validation: only a real, live YouTube session is accepted."""

from __future__ import annotations

import time

import pytest

from ytui_server.services.cookies import CookieError, CookieStore

FUTURE = int(time.time()) + 365 * 24 * 3600
PAST = int(time.time()) - 24 * 3600

HEADER = "# Netscape HTTP Cookie File\n"


def _line(name: str, value: str = "x", expires: int = FUTURE) -> str:
    return f".youtube.com\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}\n"


def _session(**overrides: int) -> str:
    return HEADER + _line("LOGIN_INFO", expires=overrides.get("login", FUTURE)) + _line(
        "SAPISID", expires=overrides.get("sapisid", FUTURE)
    )


def test_save_accepts_a_signed_in_session(tmp_path):
    store = CookieStore(tmp_path)
    store.save(_session())
    assert store.exists()
    assert (store.path.stat().st_mode & 0o777) == 0o600
    # no temp file left behind
    assert not store.path.with_suffix(".tmp").exists()


def test_save_rejects_non_netscape_text(tmp_path):
    store = CookieStore(tmp_path)
    with pytest.raises(CookieError, match="Netscape"):
        store.save('{"cookies": []}')
    assert not store.exists()


def test_save_rejects_foreign_domains_only(tmp_path):
    store = CookieStore(tmp_path)
    text = HEADER + ".example.com\tTRUE\t/\tTRUE\t%d\tLOGIN_INFO\tx\n" % FUTURE
    with pytest.raises(CookieError, match="No youtube.com cookies"):
        store.save(text)
    assert not store.exists()


def test_save_rejects_missing_login_info(tmp_path):
    store = CookieStore(tmp_path)
    with pytest.raises(CookieError, match="LOGIN_INFO"):
        store.save(HEADER + _line("SAPISID"))
    assert not store.exists()


def test_save_rejects_missing_sapisid(tmp_path):
    store = CookieStore(tmp_path)
    with pytest.raises(CookieError, match="SAPISID"):
        store.save(HEADER + _line("LOGIN_INFO"))
    assert not store.exists()


def test_save_rejects_expired_session(tmp_path):
    """yt-dlp loads expired cookies happily, so the store must check dates itself."""
    store = CookieStore(tmp_path)
    with pytest.raises(CookieError, match="LOGIN_INFO expired"):
        store.save(_session(login=PAST))
    assert not store.exists()


def test_save_keeps_the_previous_file_on_rejection(tmp_path):
    store = CookieStore(tmp_path)
    store.save(_session())
    with pytest.raises(CookieError):
        store.save("garbage")
    assert store.exists()


def test_clear_is_idempotent(tmp_path):
    store = CookieStore(tmp_path)
    store.save(_session())
    assert store.clear() is True
    assert store.clear() is False
    assert not store.exists()
