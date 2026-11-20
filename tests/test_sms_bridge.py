"""sms-bridge's core: it pulls the phone's texts through the hub and posts each once, in order.

The phone's answer has the shape in claude-voice-mobile's `docs/plugins/sms/sms-since-answer.json`
(ADR-CVM-010). The hub and the rooms here are made up, and so is every number and text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import stat

import jsonschema
import pytest

from messages_mcp import sms_bridge
from messages_mcp.sms_bridge import FIRST_WAIT, LONGEST_WAIT, BadAnswerError, SmsBridge, parse

#: The commit of claude-voice-mobile's schema this copy was taken from (#132, review round 2).
SOURCE = "01aaa6f227e5fc0cf0cea27848d0207a6609a618"
#: A copy of claude-voice-mobile's docs/plugins/sms/sms-since-answer.json (ADR-CVM-010), at #132's
#: 01aaa6f. Its one example number is written `+<country><number>`, so our private-data scan passes.
SCHEMA = json.loads((pathlib.Path(__file__).parent / "sms-since-answer.json").read_text(encoding="utf-8"))

#: A made-up number, built here so no number in international form is written in the repository.
NUMBER = "+" + "47" + "0" * 6 + "21"
WORDS = "Made-up words about a made-up dinner"


def text(ident, sender=NUMBER, kind="number", words=WORDS, *, masked=False):
    """One message as the phone gives it."""
    return {
        "id": ident,
        "from": sender,
        "from_kind": kind,
        "received_at": "2026-11-19T14:02:11+01:00",
        "text": words,
        "cut": False,
        "masked": masked,
        "untrusted": True,
    }


def answer(*messages, cursor="c-1", more=False, dropped=0, reset=False):
    """One sms_since answer."""
    return {"messages": list(messages), "cursor": cursor, "more": more, "dropped": dropped, "reset": reset}


class FakeHub:
    """The hub's call to the phone: it gives the answers it holds, in turn, and notes each cursor."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.cursors = []

    async def sms_since(self, cursor):
        self.cursors.append(cursor)
        given = self.answers.pop(0) if self.answers else answer(cursor=cursor or "c-0")
        if isinstance(given, Exception):
            raise given
        return given


class FakeRooms:
    """The Matrix side: it notes each text posted and each notice, and can fail a post."""

    def __init__(self, *, fail_at=None):
        self.posted = []
        self.notices = []
        self.fail_at = fail_at

    async def post(self, sms):
        if self.fail_at is not None and len(self.posted) == self.fail_at:
            self.fail_at = None
            msg = "the made-up homeserver did not answer"
            raise OSError(msg)
        self.posted.append(sms)

    async def notice(self, words):
        self.notices.append(words)


def bridge(tmp_path, hub, rooms=None):
    return SmsBridge(hub, rooms if rooms is not None else FakeRooms(), tmp_path / "sms-bridge" / "position.json")


def poll(b):
    return asyncio.run(b.poll())


def test_texts_are_posted_in_the_order_the_phone_gave_them(tmp_path):
    rooms = FakeRooms()
    poll(bridge(tmp_path, FakeHub(answer(text("a"), text("b", "2244", "short_code"), text("c"))), rooms))
    assert [s.ident for s in rooms.posted] == ["a", "b", "c"]
    assert (rooms.posted[1].sender, rooms.posted[1].sender_kind) == ("2244", "short_code")
    assert rooms.posted[0].received_at.utcoffset().total_seconds() == 3600


def test_the_first_poll_passes_no_cursor_and_the_next_passes_back_the_last_one(tmp_path):
    hub = FakeHub(answer(text("a"), cursor="c-7"), answer(cursor="c-7"))
    b = bridge(tmp_path, hub)
    poll(b)
    poll(b)
    assert hub.cursors == [None, "c-7"]


def test_the_cursor_is_kept_across_a_restart(tmp_path):
    poll(bridge(tmp_path, FakeHub(answer(text("a"), cursor="c-9"))))
    hub = FakeHub()
    poll(bridge(tmp_path, hub))
    assert hub.cursors == ["c-9"]


def test_the_kept_position_is_readable_by_the_owner_only(tmp_path):
    poll(bridge(tmp_path, FakeHub(answer(text("a")))))
    kept = tmp_path / "sms-bridge" / "position.json"
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600
    assert stat.S_IMODE(kept.parent.stat().st_mode) == 0o700
    assert WORDS not in kept.read_text(encoding="utf-8")
    assert NUMBER not in kept.read_text(encoding="utf-8")


def test_a_text_the_bridge_has_posted_is_never_posted_again(tmp_path):
    """A lost answer, or a phone that starts over after `reset`, gives the same ids again."""
    rooms = FakeRooms()
    hub = FakeHub(answer(text("a"), text("b"), cursor="c-2"), answer(text("a"), text("b"), text("c"), reset=True))
    b = bridge(tmp_path, hub, rooms)
    poll(b)
    poll(b)
    assert [s.ident for s in rooms.posted] == ["a", "b", "c"]


def test_the_texts_already_posted_are_remembered_across_a_restart(tmp_path):
    rooms = FakeRooms()
    poll(bridge(tmp_path, FakeHub(answer(text("a"))), rooms))
    poll(bridge(tmp_path, FakeHub(answer(text("a"), text("b"), reset=True)), rooms))
    assert [s.ident for s in rooms.posted] == ["a", "b"]


def test_the_cursor_is_kept_only_once_every_text_in_its_answer_is_posted(tmp_path):
    """A post that fails leaves the old cursor, so the phone keeps the rest and gives them again."""
    rooms = FakeRooms(fail_at=1)
    both = answer(text("a"), text("b"), cursor="c-next")
    hub = FakeHub(answer(cursor="c-old"), both, both)
    b = bridge(tmp_path, hub, rooms)
    poll(b)  # learns c-old
    poll(b)  # posts a, fails on b
    poll(b)  # passes c-old again, and posts b alone
    assert hub.cursors == [None, "c-old", "c-old"]
    assert [s.ident for s in rooms.posted] == ["a", "b"]


def test_texts_the_phone_lost_are_told_in_a_notice(tmp_path):
    rooms = FakeRooms()
    poll(bridge(tmp_path, FakeHub(answer(dropped=3)), rooms))
    assert len(rooms.notices) == 1
    assert "3 text messages" in rooms.notices[0]


def test_one_lost_text_is_told_in_the_singular(tmp_path):
    rooms = FakeRooms()
    poll(bridge(tmp_path, FakeHub(answer(dropped=1)), rooms))
    assert "1 text message " in rooms.notices[0]


def test_no_notice_is_posted_when_nothing_was_lost(tmp_path):
    rooms = FakeRooms()
    poll(bridge(tmp_path, FakeHub(answer(text("a"))), rooms))
    assert rooms.notices == []


def test_an_empty_poll_is_counted_never_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger=sms_bridge.__name__)
    b = bridge(tmp_path, FakeHub(answer(), answer()))
    poll(b)
    poll(b)
    assert caplog.records == []
    assert b.empty_polls == 2


def test_a_poll_that_posts_is_logged_with_counts_only(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger=sms_bridge.__name__)
    poll(bridge(tmp_path, FakeHub(answer(text("a"), text("b"), dropped=2))))
    said = " ".join(r.getMessage() for r in caplog.records)
    assert "posted 2" in said
    assert "2 lost" in said
    assert NUMBER not in said
    assert WORDS not in said
    assert "c-1" not in said


def test_the_bridge_waits_fifteen_seconds_after_traffic(tmp_path):
    assert poll(bridge(tmp_path, FakeHub(answer(text("a"))))) == FIRST_WAIT == 15


def test_each_empty_poll_waits_twice_as_long_up_to_two_minutes(tmp_path):
    b = bridge(tmp_path, FakeHub(answer(text("a")), *[answer()] * 5))
    waits = [poll(b) for _ in range(6)]
    assert waits == [15, 30, 60, 120, 120, 120]
    assert LONGEST_WAIT == 120


def test_new_traffic_brings_the_wait_back_to_fifteen_seconds(tmp_path):
    b = bridge(tmp_path, FakeHub(answer(), answer(), answer(text("a"))))
    assert [poll(b) for _ in range(3)] == [30, 60, 15]


def test_a_poll_with_more_waiting_asks_again_at_once(tmp_path):
    assert poll(bridge(tmp_path, FakeHub(answer(text("madeup00-1"), more=True)))) == 0


def test_a_phone_that_says_more_with_nothing_new_is_asked_again_after_a_second(tmp_path):
    """A phone that keeps saying more but gives only texts already posted must not spin."""
    b = bridge(tmp_path, FakeHub(answer(text("madeup00-1"), more=True), answer(text("madeup00-1"), more=True)))
    poll(b)
    assert poll(b) == sms_bridge.MORE_BUT_NOTHING_NEW == 1


def test_a_phone_that_does_not_answer_backs_off_like_an_empty_poll(tmp_path):
    b = bridge(tmp_path, FakeHub(OSError("away"), OSError("away"), OSError("away")))
    assert [poll(b) for _ in range(3)] == [30, 60, 120]


def test_a_phone_that_stays_away_is_logged_once(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger=sms_bridge.__name__)
    b = bridge(tmp_path, FakeHub(OSError("away"), OSError("away"), OSError("away"), answer()))
    for _ in range(4):
        poll(b)
    said = [r.getMessage() for r in caplog.records]
    assert len(said) == 2
    assert "did not answer" in said[0]
    assert "after 3 failed polls" in said[1]


def test_a_refusal_from_the_phone_is_logged_with_its_own_words(tmp_path, caplog):
    """The phone refuses only when its read permission was taken back, and says where to grant it."""
    caplog.set_level(logging.DEBUG, logger=sms_bridge.__name__)
    hub = FakeHub(sms_bridge.RefusedError("permission_missing: grant Read SMS in the app's settings"))
    poll(bridge(tmp_path, hub))
    assert "permission_missing" in caplog.records[0].getMessage()


REFUSAL = "permission_missing: grant Read SMS in the made-up app's settings"


def test_an_outage_the_phone_explains_is_told_to_the_owner_once(tmp_path):
    """The owner takes back the read permission: one notice, with the phone's words, then quiet."""
    rooms = FakeRooms()
    refused = sms_bridge.RefusedError(REFUSAL)
    b = bridge(tmp_path, FakeHub(refused, refused, refused), rooms)
    for _ in range(3):
        poll(b)
    assert len(rooms.notices) == 1
    assert REFUSAL in rooms.notices[0]


def test_an_answer_off_the_schema_is_told_to_the_owner_once(tmp_path):
    rooms = FakeRooms()
    b = bridge(tmp_path, FakeHub({"wrong": 1}, {"wrong": 1}), rooms)
    poll(b)
    poll(b)
    assert len(rooms.notices) == 1
    assert "not of the agreed shape" in rooms.notices[0]


def test_a_phone_that_is_only_away_is_not_told_to_the_owner(tmp_path):
    """A phone off the tailnet for a while is normal; its texts wait on it."""
    rooms = FakeRooms()
    poll(bridge(tmp_path, FakeHub(OSError("away")), rooms))
    assert rooms.notices == []


def test_the_owner_is_told_when_texts_come_through_again(tmp_path):
    rooms = FakeRooms()
    refused = sms_bridge.RefusedError(REFUSAL)
    b = bridge(tmp_path, FakeHub(refused, refused, answer(), answer()), rooms)
    for _ in range(4):
        poll(b)
    assert len(rooms.notices) == 2
    assert rooms.notices[1] == sms_bridge.BACK


def test_texts_coming_through_again_are_not_told_when_the_outage_was_not(tmp_path):
    rooms = FakeRooms()
    b = bridge(tmp_path, FakeHub(OSError("away"), answer()), rooms)
    poll(b)
    poll(b)
    assert rooms.notices == []


def test_a_notice_that_fails_is_tried_at_the_next_failure(tmp_path):
    class Flaky(FakeRooms):
        async def notice(self, words):
            if not self.notices and not getattr(self, "failed_once", False):
                self.failed_once = True
                msg = "the made-up homeserver did not answer"
                raise OSError(msg)
            await super().notice(words)

    rooms = Flaky()
    refused = sms_bridge.RefusedError(REFUSAL)
    b = bridge(tmp_path, FakeHub(refused, refused, refused), rooms)
    for _ in range(3):
        poll(b)
    assert len(rooms.notices) == 1


def test_a_reset_from_the_phone_is_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger=sms_bridge.__name__)
    poll(bridge(tmp_path, FakeHub(answer(reset=True))))
    assert "did not know" in caplog.records[0].getMessage()


def test_an_answer_of_the_wrong_shape_posts_nothing_and_keeps_the_cursor(tmp_path):
    rooms = FakeRooms()
    broken = answer(text("a"), cursor="c-new")
    del broken["messages"][0]["untrusted"]
    hub = FakeHub(answer(cursor="c-old"), broken)
    b = bridge(tmp_path, hub, rooms)
    poll(b)
    assert poll(b) == 60
    poll(b)
    assert rooms.posted == []
    assert hub.cursors == [None, "c-old", "c-old"]


#: Changes that take an answer off the schema; the bridge refuses each, and so does the schema.
OFF_SCHEMA = [
    lambda a: a.pop("cursor"),
    lambda a: a.update(cursor=""),
    lambda a: a.update(more="yes"),
    lambda a: a.update(dropped=-1),
    lambda a: a.update(dropped=True),
    lambda a: a.update(reset=None),
    lambda a: a.update(messages={}),
    lambda a: a.update(messages=[text(str(i)) for i in range(51)]),
    lambda a: a.update(extra=1),
    lambda a: a["messages"][0].update(id=""),
    lambda a: a["messages"][0].update(from_kind="email"),
    lambda a: a["messages"][0].update(received_at="yesterday"),
    lambda a: a["messages"][0].update(received_at="2026-11-19T14:02:11"),
    lambda a: a["messages"][0].update(received_at="2026-11-19T13:02:11Z"),
    lambda a: a["messages"][0].update(received_at=1763557331),
    lambda a: a["messages"][0].update(text=7),
    lambda a: a["messages"][0].update(masked="no"),
    lambda a: a["messages"][0].update(cut=None),
    lambda a: a["messages"][0].pop("cut"),
    lambda a: a["messages"][0].update(text="x" * 2001),
    lambda a: a["messages"][0].update(untrusted=False),
    lambda a: a["messages"][0].update(extra=1),
]


@pytest.mark.parametrize("change", OFF_SCHEMA)
def test_an_answer_off_the_schema_is_refused(change):
    given = answer(text("madeup00-1"))
    change(given)
    with pytest.raises(BadAnswerError):
        parse(given)


@pytest.mark.parametrize("change", OFF_SCHEMA)
def test_the_schema_itself_refuses_what_the_bridge_refuses(change):
    """So the copy of the schema and these checks say the same; a new copy that differs shows here."""
    given = answer(text("madeup00-1"))
    jsonschema.validate(given, SCHEMA)
    change(given)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(given, SCHEMA)


def test_a_time_that_fits_the_pattern_but_is_not_real_is_refused():
    """The schema's pattern lets it through, and 2020-12 treats `format` as a note only."""
    given = answer(text("madeup00-1"))
    given["messages"][0]["received_at"] = "2026-13-40T25:61:00+99:00"
    with pytest.raises(BadAnswerError) as refused:
        parse(given)
    assert isinstance(refused.value.__cause__, ValueError)


def test_the_schema_s_own_examples_are_read():
    """Every example in the copy of claude-voice-mobile's sms-since-answer.json is read."""
    read = [parse(example) for example in SCHEMA["examples"]]
    assert [len(a.messages) for a in read] == [2, 0, 0, 1]
    assert read[0].messages[1].masked
    assert read[2].dropped == 3
    assert (read[3].more, read[3].reset) == (True, True)


def test_a_masked_text_is_posted_as_the_phone_worded_it(tmp_path):
    rooms = FakeRooms()
    masked = text("a", "Made-up Bank", "name", "a one-time code from Made-up Bank", masked=True)
    poll(bridge(tmp_path, FakeHub(answer(masked)), rooms))
    assert rooms.posted[0].text == "a one-time code from Made-up Bank"
    assert rooms.posted[0].masked


def test_a_broken_position_file_starts_over_from_the_phone_s_oldest(tmp_path, caplog):
    kept = tmp_path / "sms-bridge" / "position.json"
    kept.parent.mkdir(mode=0o700)
    kept.write_text("not json", encoding="utf-8")
    hub = FakeHub()
    caplog.set_level(logging.DEBUG, logger=sms_bridge.__name__)
    poll(bridge(tmp_path, hub))
    assert hub.cursors == [None]
    assert "starts over" in caplog.records[0].getMessage()


def test_only_the_newest_ids_are_remembered(tmp_path):
    """The phone keeps at most 500, so twice that is enough to drop every repeat."""
    many = [text(str(i)) for i in range(50)]
    b = bridge(tmp_path, FakeHub(*[answer(*many[i : i + 1], cursor=f"c-{i}") for i in range(50)]))
    b.keep_seen = 10
    for _ in range(50):
        poll(b)
    kept = json.loads((tmp_path / "sms-bridge" / "position.json").read_text(encoding="utf-8"))
    assert len(kept["seen"]) == 10


def test_twice_the_phone_s_queue_of_ids_is_remembered():
    """The phone keeps at most 500 texts, so a reset can give back no more than that."""
    assert sms_bridge.KEEP_SEEN >= 2 * 500


def test_run_polls_until_stopped_and_waits_as_each_poll_says(tmp_path):
    waited = []
    stop = asyncio.Event()

    async def sleep(seconds):
        waited.append(seconds)
        if len(waited) == 3:
            stop.set()

    b = bridge(tmp_path, FakeHub(answer(text("a"), more=True), answer(text("b")), answer()))
    asyncio.run(b.run(stop, sleep=sleep))
    assert waited == [0, 15, 30]
