"""sms-bridge's Matrix side: one room per sender on the laptop's own Synapse, as an appservice.

Every request goes to a made-up Synapse (`appservice_home.py`). Every number, name and text is
made up, and the numbers are built at run time so no number in international form is written here.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import stat

import pytest
import yaml
from appservice_home import AS_TOKEN, BOT, OWNER, SERVER, serving

from messages_mcp import sms_rooms
from messages_mcp.device import NotSetUpError
from messages_mcp.sms_bridge import SmsBridge
from messages_mcp.sms_rooms import MatrixRooms, RoomsError, ghost_of, registration, write_registration

NUMBER = "+" + "47" + "0" * 6 + "21"
OTHER = "+" + "47" + "0" * 6 + "22"
WORDS = "Made-up words about a made-up dinner"
AT = dt.datetime(2026, 11, 19, 14, 2, 11, tzinfo=dt.timezone(dt.timedelta(hours=1)))


def sms(ident="madeup00-1", sender=NUMBER, kind="number", words=WORDS, *, masked=False, cut=False):  # noqa: PLR0913 - a text's own fields
    return sms_rooms.Sms(ident, sender, kind, AT, words, masked, cut)


def rooms(server, tmp_path, homeserver=None):
    return MatrixRooms(homeserver or server.url, AS_TOKEN, OWNER, tmp_path / "sms-bridge" / "rooms.json")


def run(steps):
    """Run `steps(server)` against a fresh made-up Synapse, and give the server back."""

    async def go():
        async with serving() as server:
            await steps(server)
            return server

    return asyncio.run(go())


def test_a_first_text_makes_a_direct_room_marked_as_an_sms_chat(tmp_path):
    async def steps(server):
        await rooms(server, tmp_path).post(sms())

    server = run(steps)
    [(room, made)] = server.rooms.items()
    assert made["creator"] == BOT
    assert made["made"]["is_direct"] is True
    assert set(made["made"]["invite"]) == {OWNER, ghost_of(NUMBER, "number", SERVER)}
    [bridge] = [s for s in made["made"]["initial_state"] if s["type"] == "m.bridge"]
    assert bridge["content"]["bridgebot"] == BOT
    assert bridge["content"]["protocol"]["id"] == "sms"
    assert bridge["content"]["com.beeper.room_type"] == "dm"
    assert made["made"]["name"] == NUMBER
    assert ghost_of(NUMBER, "number", SERVER) in made["joined"]
    assert len(server.messages(room)) == 1


def test_a_text_is_sent_as_its_sender_at_the_time_the_phone_received_it(tmp_path):
    async def steps(server):
        await rooms(server, tmp_path).post(sms())

    server = run(steps)
    [room] = server.rooms
    [event] = server.messages(room)
    assert event["sender"] == ghost_of(NUMBER, "number", SERVER)
    assert event["ts"] == str(int(AT.timestamp() * 1000))
    assert event["content"]["msgtype"] == "m.text"
    assert event["content"]["body"] == WORDS


def test_a_sender_keeps_one_room(tmp_path):
    async def steps(server):
        here = rooms(server, tmp_path)
        await here.post(sms("madeup00-1"))
        await here.post(sms("madeup00-2"))
        await rooms(server, tmp_path).post(sms("madeup00-3"))  # after a restart

    server = run(steps)
    [room] = server.rooms
    assert len(server.messages(room)) == 3
    assert len(server.asked("POST", "/createRoom")) == 1


def test_each_sender_has_a_room_of_its_own(tmp_path):
    async def steps(server):
        here = rooms(server, tmp_path)
        await here.post(sms("madeup00-1"))
        await here.post(sms("madeup00-2", OTHER))
        await here.post(sms("madeup00-3", "2244", "short_code"))
        await here.post(sms("madeup00-4", "Made-up Bank", "name"))

    server = run(steps)
    assert len(server.rooms) == 4
    assert {r["made"]["name"] for r in server.rooms.values()} == {NUMBER, OTHER, "2244", "Made-up Bank"}


def test_a_retried_text_reuses_its_transaction_id(tmp_path):
    """Synapse takes a transaction once, so a text posted again after a lost answer is not doubled."""

    async def steps(server):
        here = rooms(server, tmp_path)
        await here.post(sms("madeup00-7"))
        await here.post(sms("madeup00-7"))

    server = run(steps)
    [room] = server.rooms
    assert len(server.messages(room)) == 1
    sends = server.asked("PUT", "/send/")
    assert len(sends) == 2
    assert sends[0]["path"] == sends[1]["path"]


def test_a_ghost_is_registered_once_and_named_as_the_phone_names_its_sender(tmp_path):
    async def steps(server):
        here = rooms(server, tmp_path)
        await here.post(sms("madeup00-1", "Made-up Bank", "name"))
        await here.post(sms("madeup00-2", "Made-up Bank", "name"))

    server = run(steps)
    ghost = ghost_of("Made-up Bank", "name", SERVER)
    assert server.users == {ghost}
    assert server.names[ghost] == "Made-up Bank"


def test_a_name_is_never_part_of_a_matrix_id():
    ghost = ghost_of("Made-up Bank", "name", SERVER)
    assert "bank" not in ghost.lower()
    assert re.fullmatch(r"@sms_name_[0-9a-f]{20}:" + re.escape(SERVER), ghost)
    assert ghost == ghost_of("MADE-UP BANK", "name", SERVER)


def test_ghost_ids_are_valid_matrix_ids_and_inside_the_bridge_s_namespace():
    namespace = re.compile(registration(SERVER)["namespaces"]["users"][0]["regex"])
    for sender, kind in [(NUMBER, "number"), ("2244", "short_code"), ("Made-up Bank", "name"), ("+12", "number")]:
        ghost = ghost_of(sender, kind, SERVER)
        assert namespace.fullmatch(ghost), ghost
        assert re.fullmatch(r"@[a-z0-9._=/-]+:" + re.escape(SERVER), ghost), ghost
    assert ghost_of(NUMBER, "number", SERVER) == f"@sms_{NUMBER[1:]}:{SERVER}"
    assert ghost_of("2244", "short_code", SERVER) == f"@sms_short_2244:{SERVER}"


def test_a_number_of_an_odd_shape_is_hashed_like_a_name():
    """The phone says `number`, but it is not E.164: it still gets a room, never a broken ID."""
    spaced = f"{NUMBER[:3]} {NUMBER[3:6]} {NUMBER[6:8]} {NUMBER[8:]}"
    ghost = ghost_of(spaced, "number", SERVER)
    assert ghost.startswith("@sms_name_")


def test_the_flags_of_a_masked_or_cut_text_go_with_it(tmp_path):
    async def steps(server):
        here = rooms(server, tmp_path)
        await here.post(sms("madeup00-1", "Made-up Bank", "name", "a one-time code from Made-up Bank", masked=True))
        await here.post(sms("madeup00-2", cut=True))

    server = run(steps)
    events = [e for room in server.rooms for e in server.messages(room)]
    flags = {e["content"]["body"]: e["content"][sms_rooms.FLAGS] for e in events}
    assert flags["a one-time code from Made-up Bank"] == {"masked": True, "cut": False}
    assert flags[WORDS] == {"masked": False, "cut": True}


def test_notices_go_to_one_bridge_room_that_is_never_marked_as_a_chat(tmp_path):
    async def steps(server):
        here = rooms(server, tmp_path)
        await here.notice("first made-up notice")
        await rooms(server, tmp_path).notice("second made-up notice")

    server = run(steps)
    [(room, made)] = server.rooms.items()
    assert made["made"]["invite"] == [OWNER]
    assert all(s["type"] != "m.bridge" for s in made["made"].get("initial_state", []))
    events = server.messages(room)
    assert [e["content"]["body"] for e in events] == ["first made-up notice", "second made-up notice"]
    assert {e["content"]["msgtype"] for e in events} == {"m.notice"}
    assert {e["sender"] for e in events} == {BOT}


def test_the_room_list_is_readable_by_the_owner_only(tmp_path):
    async def steps(server):
        await rooms(server, tmp_path).post(sms())

    run(steps)
    kept = tmp_path / "sms-bridge" / "rooms.json"
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600
    assert stat.S_IMODE(kept.parent.stat().st_mode) == 0o700


def test_a_refused_request_is_an_error_naming_no_one(tmp_path):
    async def steps(server):
        server.fail_next.append("/createRoom")
        with pytest.raises(RoomsError) as failed:
            await rooms(server, tmp_path).post(sms())
        assert "500" in str(failed.value)
        assert NUMBER not in str(failed.value)
        assert WORDS not in str(failed.value)

    run(steps)


def test_a_text_whose_send_failed_is_sent_into_the_same_room_next_time(tmp_path):
    async def steps(server):
        server.fail_next.append("/send/")
        here = rooms(server, tmp_path)
        with pytest.raises(RoomsError):
            await here.post(sms())
        await here.post(sms())

    server = run(steps)
    [room] = server.rooms
    assert len(server.messages(room)) == 1


def test_only_a_synapse_on_this_laptop_gets_the_bridges_token(tmp_path):
    async def steps(server):
        away = rooms(server, tmp_path, homeserver="https://made-up-host.example.invalid")
        with pytest.raises(NotSetUpError):
            await away.post(sms())

    server = run(steps)
    assert server.requests == []


def test_nothing_is_logged_with_a_number_a_name_or_a_text(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)

    async def steps(server):
        here = rooms(server, tmp_path)
        await here.post(sms())
        await here.post(sms("madeup00-2", "Made-up Bank", "name"))
        await here.notice("made-up notice")

    run(steps)
    said = " ".join(r.getMessage() for r in caplog.records)
    for private in (NUMBER, NUMBER[1:], "Made-up Bank", WORDS, "made-up notice"):
        assert private not in said


def test_the_bridge_posts_through_the_rooms_in_the_phone_s_order(tmp_path):
    """The core and the Matrix side together: a made-up phone's answer lands in Synapse as given."""

    class Phone:
        def __init__(self):
            self.given = False

        async def sms_since(self, _cursor):
            texts = [] if self.given else [_raw("madeup00-1", NUMBER, "one"), _raw("madeup00-2", NUMBER, "two")]
            self.given = True
            return {"messages": texts, "cursor": "c-1", "more": False, "dropped": 0, "reset": False}

    async def steps(server):
        bridge = SmsBridge(Phone(), rooms(server, tmp_path), tmp_path / "sms-bridge" / "position.json")
        await bridge.poll()
        await bridge.poll()

    server = run(steps)
    [room] = server.rooms
    assert [e["content"]["body"] for e in server.messages(room)] == ["one", "two"]


def _raw(ident, sender, words):
    return {
        "id": ident,
        "from": sender,
        "from_kind": "number",
        "received_at": "2026-11-19T14:02:11+01:00",
        "text": words,
        "cut": False,
        "masked": False,
        "untrusted": True,
    }


def test_the_registration_lets_the_bridge_own_only_its_ghosts():
    made = registration(SERVER)
    assert made["sender_localpart"] == "smsbot"
    assert made["url"] is None
    assert made["rate_limited"] is False
    assert made["receive_ephemeral"] is False
    [users] = made["namespaces"]["users"]
    assert users["exclusive"] is True
    namespace = re.compile(users["regex"])
    assert namespace.fullmatch(f"@sms_4700000021:{SERVER}")
    assert not namespace.fullmatch(OWNER)
    assert not namespace.fullmatch("@sms_4700000021:made-up-other.example.invalid")
    assert made["namespaces"]["rooms"] == []
    assert made["namespaces"]["aliases"] == []
    assert len(made["as_token"]) >= 40
    assert len(made["hs_token"]) >= 40
    assert made["as_token"] != made["hs_token"]


def test_the_registration_is_written_for_the_owner_only(tmp_path):
    path = tmp_path / "sms-bridge" / "registration.yaml"
    assert write_registration(path, SERVER) is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["sender_localpart"] == "smsbot"


def test_the_registration_is_never_overwritten(tmp_path):
    """Synapse holds its tokens; new ones would lock the bridge out until Synapse read them again."""
    path = tmp_path / "sms-bridge" / "registration.yaml"
    write_registration(path, SERVER)
    first = path.read_text(encoding="utf-8")
    assert write_registration(path, SERVER) is False
    assert path.read_text(encoding="utf-8") == first


def test_the_bridge_s_token_is_read_from_its_registration(tmp_path):
    path = tmp_path / "sms-bridge" / "registration.yaml"
    write_registration(path, SERVER)
    assert sms_rooms.as_token(path) == yaml.safe_load(path.read_text(encoding="utf-8"))["as_token"]


def test_a_missing_registration_says_how_to_make_one(tmp_path):
    with pytest.raises(NotSetUpError, match="registration"):
        sms_rooms.as_token(tmp_path / "nowhere.yaml")


def test_a_registration_without_a_token_says_how_to_make_a_new_one(tmp_path):
    path = tmp_path / "registration.yaml"
    path.write_text("as_token: ''\n", encoding="utf-8")
    with pytest.raises(NotSetUpError, match="no token"):
        sms_rooms.as_token(path)


def test_a_broken_room_list_is_an_error_not_a_second_room(tmp_path):
    """Starting over would make a second room for a sender who already has one."""
    kept = tmp_path / "sms-bridge" / "rooms.json"
    kept.parent.mkdir(mode=0o700)
    kept.write_text("not json", encoding="utf-8")

    async def steps(server):
        with pytest.raises(RoomsError, match="could not be read"):
            await rooms(server, tmp_path).post(sms())

    server = run(steps)
    assert server.rooms == {}


def test_a_synapse_that_does_not_answer_is_an_error_naming_no_one(tmp_path):
    async def steps(server):
        await server.__aexit__()
        with pytest.raises(RoomsError) as failed:
            await rooms(server, tmp_path).post(sms())
        assert "did not get an answer" in str(failed.value)
        assert NUMBER not in str(failed.value)
        await server.__aenter__()

    run(steps)


def test_a_ghost_registered_before_is_taken_as_it_is(tmp_path):
    """A room list lost with Synapse kept: the ghost is there already, and that is fine."""

    async def steps(server):
        await rooms(server, tmp_path / "first").post(sms())
        await rooms(server, tmp_path / "second").post(sms("madeup00-2"))

    server = run(steps)
    assert len(server.asked("POST", "/register")) == 2
    assert len(server.users) == 1


def test_texts_from_an_unknown_sender_share_one_room(tmp_path):
    """The phone names a text with no sender 'an unknown sender', of kind name (mobile #137)."""

    async def steps(server):
        here = rooms(server, tmp_path)
        for ident in ("madeup00-1", "madeup00-2"):
            await here.post(sms(ident, sender="an unknown sender", kind="name"))

    server = run(steps)
    [room] = server.rooms
    assert len(server.messages(room)) == 2
    assert ghost_of("an unknown sender", "name", SERVER).startswith("@sms_name_")


def test_a_room_is_kept_as_soon_as_it_is_made(tmp_path):
    """A join that fails once leaves the room kept, so the sender's next text finds it."""

    async def steps(server):
        server.fail_next.append("/join/")
        here = rooms(server, tmp_path)
        with pytest.raises(RoomsError):
            await here.post(sms())
        await here.post(sms())

    server = run(steps)
    [(room, made)] = server.rooms.items()
    assert ghost_of(NUMBER, "number", SERVER) in made["joined"]
    assert len(server.messages(room)) == 1


def test_two_first_texts_at_once_from_a_sender_make_one_room(tmp_path):
    async def steps(server):
        here = rooms(server, tmp_path)
        await asyncio.gather(here.post(sms("madeup00-1")), here.post(sms("madeup00-2")))

    server = run(steps)
    [room] = server.rooms
    assert len(server.messages(room)) == 2


def test_a_room_list_that_is_not_one_is_an_error_not_a_fresh_start(tmp_path):
    kept = tmp_path / "sms-bridge" / "rooms.json"
    kept.parent.mkdir(mode=0o700)
    kept.write_text("[]", encoding="utf-8")

    async def steps(server):
        with pytest.raises(RoomsError, match="not a list of rooms"):
            await rooms(server, tmp_path).post(sms())

    server = run(steps)
    assert server.rooms == {}
