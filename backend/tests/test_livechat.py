"""Live-chat parsers, cursor-delta buffer logic and the endpoint guard."""

from __future__ import annotations

from ytui_server.models import ChatMessage
from ytui_server.services.livechat import (
    YT_DEFAULT_TIMEOUT_MS,
    ChatManager,
    _find_json,
    _parse_live_actions,
    _parse_privmsg,
    _Room,
)

PRIVMSG_LINE = (
    "@color=#FF0000;display-name=Foo;id=abc;tmi-sent-ts=1700000000000 "
    ":foo!foo@foo.tmi.twitch.tv PRIVMSG #chan :hello"
)

LCC = {
    "actions": [
        {
            "addChatItemAction": {
                "item": {
                    "liveChatTextMessageRenderer": {
                        "id": "msg1",
                        "authorName": {"simpleText": "Alice"},
                        "message": {
                            "runs": [
                                {"text": "hello "},
                                {"emoji": {"shortcuts": [":smile:"]}},
                            ]
                        },
                        "timestampUsec": "1700000000000000",
                    }
                }
            }
        }
    ],
    "continuations": [
        {"invalidationContinuationData": {"continuation": "CONT_TOKEN", "timeoutMs": 8000}}
    ],
}


def test_parse_privmsg():
    m = _parse_privmsg(PRIVMSG_LINE)
    assert m is not None
    assert m.author == "Foo"
    assert m.text == "hello"
    assert m.color == "#FF0000"
    assert m.id == "abc"
    assert m.timestamp == 1700000000.0


def test_parse_privmsg_ping_and_join_ignored():
    assert _parse_privmsg("PING :tmi.twitch.tv") is None
    assert _parse_privmsg(":nick!nick@nick.tmi.twitch.tv JOIN #chan") is None


def test_parse_privmsg_without_tags_uses_nick():
    m = _parse_privmsg(":bob!bob@bob.tmi.twitch.tv PRIVMSG #chan :hi there")
    assert m is not None
    assert m.author == "bob"
    assert m.text == "hi there"
    assert m.color is None
    assert m.id == "bob-"
    assert m.timestamp == 0.0


def test_parse_privmsg_rejects_non_hex_color():
    # 7 chars but not hex: would crash mobile int.parse / TUI Rich markup if passed through.
    line = (
        "@color=#GGGGGG;display-name=Foo;id=x;tmi-sent-ts=0 "
        ":foo!foo@foo.tmi.twitch.tv PRIVMSG #chan :hi"
    )
    m = _parse_privmsg(line)
    assert m is not None
    assert m.color is None


def test_parse_live_actions():
    msgs, cont, timeout = _parse_live_actions(LCC)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.id == "msg1"
    assert m.author == "Alice"
    assert m.text == "hello :smile:"
    assert m.timestamp == 1700000000.0
    assert cont == "CONT_TOKEN"
    assert timeout == 8000


def test_parse_live_actions_skips_non_text_and_defaults_timeout():
    lcc = {"actions": [{"addChatItemAction": {"item": {"liveChatPaidMessageRenderer": {}}}}]}
    msgs, cont, timeout = _parse_live_actions(lcc)
    assert msgs == []
    assert cont is None
    assert timeout == YT_DEFAULT_TIMEOUT_MS


async def test_chat_manager_cursor_delta():
    m = ChatManager()
    # Pre-seed a room (task=None) so poll() never opens a real upstream session.
    room = _Room()
    for i in range(3):
        room.add(ChatMessage(id=str(i), author="a", text=f"m{i}"))
    m._rooms["youtube:vid"] = room

    first = await m.poll("youtube", "vid", 0)
    assert [msg.text for msg in first.messages] == ["m0", "m1", "m2"]
    assert first.cursor == 3
    assert first.active is True

    delta = await m.poll("youtube", "vid", first.cursor)
    assert delta.messages == []
    assert delta.cursor == 3

    stale = await m.poll("youtube", "vid", 9999)
    assert [msg.text for msg in stale.messages] == ["m0", "m1", "m2"]
    assert stale.cursor == 3

    await m.shutdown()
    assert m._rooms == {}


def test_chat_unsupported_platform(client):
    r = client.get("/api/lives/x/chat?platform=odysee")
    assert r.status_code == 501


def test_find_json_skips_unparseable_match():
    # First 'ytInitialData' points at a single-quoted JS object (invalid JSON);
    # _find_json must skip it and return the real assignment's JSON.
    s = "if(ytInitialData){'x':1}; var ytInitialData = {\"a\": {\"b\": 2}};"
    assert _find_json(s, "ytInitialData") == {"a": {"b": 2}}


def test_find_json_none_when_absent():
    assert _find_json("nothing here", "ytInitialData") is None


# ─── room lifecycle and Twitch reconnects ───

import asyncio  # noqa: E402

import pytest  # noqa: E402

from ytui_server.services import livechat  # noqa: E402
from ytui_server.services.livechat import _irc_command  # noqa: E402


def test_irc_command():
    assert _irc_command("PING :tmi.twitch.tv") == "PING"
    assert _irc_command(":tmi.twitch.tv RECONNECT") == "RECONNECT"
    assert _irc_command(":tmi.twitch.tv 001 justinfan1 :Welcome, GLHF!") == "001"
    assert _irc_command(PRIVMSG_LINE) == "PRIVMSG"
    # A chat message quoting RECONNECT is still a PRIVMSG.
    assert _irc_command(":a!a@a PRIVMSG #c :RECONNECT") == "PRIVMSG"


async def test_idle_room_closes_itself_without_another_poll(monkeypatch):
    """The last client leaving is enough: nobody else needs to poll."""
    monkeypatch.setattr(livechat, "IDLE_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(livechat, "CHAT_ROOM_IDLE_SECONDS", 0.05)
    stopped = asyncio.Event()

    async def upstream(self, platform, video_id, room):
        try:
            await asyncio.sleep(3600)
        finally:
            stopped.set()

    monkeypatch.setattr(ChatManager, "_run", upstream)
    m = ChatManager()
    await m.poll("twitch", "someone:1", 0)
    assert "twitch:someone:1" in m._rooms
    await asyncio.wait_for(stopped.wait(), timeout=2)
    await asyncio.sleep(0.02)
    assert m._rooms == {}


async def test_twitch_reconnects_then_gives_up(monkeypatch):
    sessions = []

    async def fake_session(self, login, room):
        sessions.append(login)
        if len(sessions) == 1:
            return True  # server-requested RECONNECT after a healthy session
        raise OSError("network down")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(ChatManager, "_twitch_session", fake_session)
    monkeypatch.setattr(livechat.asyncio, "sleep", no_sleep)
    await ChatManager()._run_twitch("SomeLogin:123", _Room())
    assert sessions == ["somelogin"] * (1 + livechat.TWITCH_MAX_FAILURES)


@pytest.mark.parametrize("video_id", ["bad login:1", "a#b:1", "x\r\nPRIVMSG:1"])
async def test_twitch_rejects_invalid_login(monkeypatch, video_id):
    async def fake_session(self, login, room):
        raise AssertionError("must not connect")

    monkeypatch.setattr(ChatManager, "_twitch_session", fake_session)
    await ChatManager()._run_twitch(video_id, _Room())
