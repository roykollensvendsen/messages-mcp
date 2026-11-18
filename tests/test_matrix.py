"""The Matrix reader, against a made-up Synapse: what it asks, what it reads, and what it never does."""

import asyncio
import json
import logging

import aiohttp
import pytest
from homeserver import ELEMENT_TOKEN, OWNER, PLUGIN_TOKEN, plain, portal, serving, text

from messages_mcp.device import NotSetUpError, UnreachableError
from messages_mcp.matrix import MatrixInbox, bot_of, on_this_laptop

FRIEND = "@meta_made-up-1:made-up"
COACH = "@meta_made-up-2:made-up"
ROOMS = {
    "!friend:made-up": portal(
        "Made-up Friend",
        "made-up-login-1",
        [text(FRIEND, "Made-up hello", 1_000_000), text(FRIEND, "Made-up second line", 2_000_000)],
        members={FRIEND: "Made-up Friend"},
    ),
    "!group:made-up": portal(
        "Made-up Group",
        "made-up-login-2",
        [text(COACH, "", 3_000_000, msgtype="m.image")],
        members={COACH: "Made-up Coach"},
    ),
    "!bot:made-up": plain("Made-up management room", [text("@bridgebot:made-up", "Made-up bot text", 4_000_000)]),
    "!other:made-up": portal("Made-up other network", "x", [text(FRIEND, "Made-up", 5)], protocol="made-up-net"),
}


def _login(tmp_path, url):
    path = tmp_path / "matrix-login.json"
    path.write_text(json.dumps({"homeserver": url, "access_token": PLUGIN_TOKEN, "user_id": OWNER}))
    return path


def _read(tmp_path, rooms=ROOMS, unread=None):
    async def scenario():
        async with serving(rooms, unread) as server:
            chats = await MatrixInbox(_login(tmp_path, server.url)).chats()
            return chats, server

    return asyncio.run(scenario())


def test_the_plugin_asks_synapse_only_for_a_sync_and_sends_nothing(tmp_path):
    """Wall 2: one GET /sync, presence offline, no wait; never a receipt, typing or presence."""
    _, server = _read(tmp_path)
    asked = server.asked_by(PLUGIN_TOKEN)
    assert [(method, path) for method, path, _ in asked] == [("GET", "/_matrix/client/v3/sync")]
    query = asked[0][2]
    assert query["set_presence"] == "offline"
    assert query["timeout"] == "0"
    assert json.loads(query["filter"])["presence"] == {"not_types": ["*"]}


def test_the_reader_has_no_way_to_send():
    """Its token could send as the owner, so the reader holds no method that could."""
    public = {name for name in dir(MatrixInbox) if not name.startswith("_")}
    assert public == {"chats"}


def test_only_the_bridges_messenger_rooms_are_read(tmp_path):
    chats, _ = _read(tmp_path)
    assert sorted(chat.name for chat in chats) == ["Made-up Friend", "Made-up Group"]


def test_a_portal_names_its_login_its_senders_and_what_was_sent(tmp_path):
    chats = {chat.name: chat for chat in _read(tmp_path)[0]}
    friend = chats["Made-up Friend"]
    assert friend.receiver == "made-up-login-1"
    assert [(m.sender, m.body, m.kind) for m in friend.messages] == [
        ("Made-up Friend", "Made-up hello", "text"),
        ("Made-up Friend", "Made-up second line", "text"),
    ]
    assert friend.last_at.timestamp() == 2_000
    picture = chats["Made-up Group"].messages[0]
    assert (picture.sender, picture.body, picture.kind) == ("Made-up Coach", "", "picture")


def test_a_sender_with_no_name_is_called_by_their_user_name(tmp_path):
    rooms = {"!r:made-up": portal("Made-up Chat", "made-up-login-1", [text(FRIEND, "Made-up", 1)])}
    assert _read(tmp_path, rooms)[0][0].messages[0].sender == "meta_made-up-1"


def test_an_encrypted_message_is_named_not_read(tmp_path):
    rooms = {"!r:made-up": portal("Made-up Chat", "made-up-login-1", [], encrypted=[(FRIEND, 7)])}
    message = _read(tmp_path, rooms)[0][0].messages[0]
    assert (message.kind, message.body) == ("encrypted message", "")


def test_a_portal_with_no_login_has_none(tmp_path):
    rooms = {"!r:made-up": portal("Made-up Chat", "", [text(FRIEND, "Made-up", 1)])}
    assert _read(tmp_path, rooms)[0][0].receiver == ""


def test_the_count_drops_when_the_owner_reads_in_element_with_a_private_receipt(tmp_path):
    """Element sends only m.read.private (owner step E); Synapse's count follows it, the plugin sends none."""

    async def scenario():
        async with serving(ROOMS, {"!friend:made-up": 2}) as server:
            inbox = MatrixInbox(_login(tmp_path, server.url))
            before = {c.name: c.unread for c in await inbox.chats()}
            receipt = f"{server.url}/_matrix/client/v3/rooms/!friend:made-up/receipt/m.read.private/$made-up-1"
            headers = {"Authorization": f"Bearer {ELEMENT_TOKEN}"}
            async with aiohttp.ClientSession() as element, element.post(receipt, json={}, headers=headers) as sent:
                assert sent.status == 200
            after = {c.name: c.unread for c in await inbox.chats()}
            return before, after, server

    before, after, server = asyncio.run(scenario())
    assert before["Made-up Friend"] == 2
    assert after["Made-up Friend"] == 0
    assert all(method == "GET" for method, _, _ in server.asked_by(PLUGIN_TOKEN))


def test_a_read_logs_counts_and_never_a_name_or_a_word(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    _read(tmp_path)
    assert "read 2 bridged chats of 4 rooms" in caplog.text
    for private in ("Made-up Friend", "Made-up hello", "Made-up Coach", "made-up-login", "!friend", "Made-up Group"):
        assert private not in caplog.text


def test_with_no_login_nothing_is_asked(tmp_path):
    with pytest.raises(NotSetUpError, match="messages-mcp login"):
        asyncio.run(MatrixInbox(tmp_path / "missing.json").chats())


def test_a_login_file_without_a_token_is_no_login(tmp_path):
    path = tmp_path / "matrix-login.json"
    path.write_text(json.dumps({"homeserver": "http://127.0.0.1:9"}))
    with pytest.raises(NotSetUpError):
        asyncio.run(MatrixInbox(path).chats())


def test_a_synapse_that_is_down_is_unreachable(tmp_path):
    path = tmp_path / "matrix-login.json"
    # Port 9 (discard) has no listener here, so the connection is refused at once.
    login = {"homeserver": "http://127.0.0.1:9", "access_token": PLUGIN_TOKEN, "user_id": OWNER}
    path.write_text(json.dumps(login))
    with pytest.raises(UnreachableError):
        asyncio.run(MatrixInbox(path).chats())


def test_a_reaction_or_a_state_change_is_no_message(tmp_path):
    room = portal("Made-up Chat", "made-up-login-1", [text(FRIEND, "Made-up", 1)])
    room["timeline"] += [
        {"type": "m.reaction", "content": {}, "sender": FRIEND, "origin_server_ts": 2},
        {"type": "m.room.message", "content": {}, "sender": FRIEND, "origin_server_ts": 3, "state_key": ""},
    ]
    assert [m.body for m in _read(tmp_path, {"!r:made-up": room})[0][0].messages] == ["Made-up"]


def test_an_edit_is_no_new_message_and_the_owners_own_is_marked(tmp_path):
    room = portal("Made-up Chat", "made-up-login-1", [text(FRIEND, "Made-up", 1), text(OWNER, "Made-up reply", 2)])
    edit = {
        "msgtype": "m.text",
        "body": "* Made-up",
        "m.relates_to": {"rel_type": "m.replace", "event_id": "$made-up-0"},
    }
    room["timeline"].append({"type": "m.room.message", "content": edit, "sender": FRIEND, "origin_server_ts": 3})
    messages = _read(tmp_path, {"!r:made-up": room})[0][0].messages
    assert [(m.body, m.own) for m in messages] == [("Made-up", False), ("Made-up reply", True)]


def test_the_plugin_talks_only_to_a_synapse_on_this_laptop(tmp_path):
    """Its token can send as the owner, so it is never sent across a network (quality f1 on #7)."""
    assert on_this_laptop("http://127.0.0.1:8008")
    assert on_this_laptop("http://[::1]:8008")
    assert on_this_laptop("http://localhost:8008")
    assert not on_this_laptop("https://made-up-laptop.example.invalid:8448")
    assert not on_this_laptop("http://127.0.0.1.example.invalid")
    path = tmp_path / "matrix-login.json"
    path.write_text(json.dumps({"homeserver": "https://made-up.example.invalid", "access_token": PLUGIN_TOKEN}))
    with pytest.raises(NotSetUpError, match="not on this laptop"):
        asyncio.run(MatrixInbox(path).chats())


def test_only_the_bridge_bot_can_make_a_room_a_portal(tmp_path):
    """voice-architect f1 on #7: a member who sets an m.bridge state of their own makes no portal."""
    rooms = {
        "!real:made-up": portal("Made-up Real", "made-up-login-1", [text(FRIEND, "Made-up", 1)]),
        "!forged:made-up": portal("Made-up Forged", "made-up-login-1", [text(FRIEND, "Made-up", 2)], sent_by=FRIEND),
        "!other-server:made-up": portal(
            "Made-up Elsewhere", "made-up-login-1", [text(FRIEND, "Made-up", 3)], sent_by="@facebookbot:made-up.invalid"
        ),
    }
    assert [chat.name for chat in _read(tmp_path, rooms)[0]] == ["Made-up Real"]


def test_a_later_forged_m_bridge_state_does_not_hide_the_bots(tmp_path):
    room = portal("Made-up Chat", "made-up-login-1", [text(FRIEND, "Made-up", 1)])
    forged = dict(room["state"][0], sender=FRIEND, content={"protocol": {"id": "made-up-net"}})
    room["timeline"].append(forged)
    assert [chat.name for chat in _read(tmp_path, {"!r:made-up": room})[0]] == ["Made-up Chat"]


def test_the_bot_is_on_the_owners_own_server():
    assert bot_of(OWNER) == "@facebookbot:made-up-laptop.example.invalid"


def test_a_login_that_names_no_user_is_no_login(tmp_path):
    path = tmp_path / "matrix-login.json"
    path.write_text(json.dumps({"homeserver": "http://127.0.0.1:9", "access_token": PLUGIN_TOKEN}))
    with pytest.raises(NotSetUpError, match="names no Matrix user"):
        asyncio.run(MatrixInbox(path).chats())
