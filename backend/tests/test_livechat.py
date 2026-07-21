"""Live-chat parsers, cursor-delta buffer logic and the endpoint guard."""

from __future__ import annotations

from ytui_server.models import ChatMessage
from ytui_server.services.livechat import (
    YT_DEFAULT_TIMEOUT_MS,
    ChatManager,
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
