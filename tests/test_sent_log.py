"""sent.jsonl: when, which account, a hash of the room, the length and the outcome; no text, no names."""

import json
import stat

from messages_mcp.sent_log import KEEP_DAYS, SentLog


class _Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_a_line_holds_no_text_and_no_room_id(tmp_path):
    path = tmp_path / "state" / "sent.jsonl"
    log = SentLog(path, clock=_Clock())
    log.write("made-up-id", "account 1", "!made-up-room:made-up", 27, "sent")
    [line] = _lines(path)
    assert set(line) == {"at", "id", "account", "room", "length", "outcome"}
    assert (line["account"], line["length"], line["outcome"]) == ("account 1", 27, "sent")
    assert "made-up-room" not in json.dumps(line)
    assert len(line["room"]) == 16


def test_the_log_is_readable_by_the_owner_only(tmp_path):
    path = tmp_path / "state" / "sent.jsonl"
    SentLog(path, clock=_Clock()).write("made-up-id", None, "!r:made-up", 1, "held")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_held_message_with_no_outcome_is_dropped_on_restart(tmp_path):
    path = tmp_path / "sent.jsonl"
    clock = _Clock()
    before = SentLog(path, clock=clock)
    before.write("made-up-1", None, "!r:made-up", 5, "held")
    before.write("made-up-1", None, "!r:made-up", 5, "sent")
    before.write("made-up-2", None, "!r:made-up", 5, "held")
    assert SentLog(path, clock=clock).drop_unfinished() == 1
    assert [(line["id"], line["outcome"]) for line in _lines(path)][-1] == ("made-up-2", "dropped")
    assert SentLog(path, clock=clock).drop_unfinished() == 0


def test_lines_older_than_ninety_days_are_dropped(tmp_path):
    path = tmp_path / "sent.jsonl"
    clock = _Clock()
    log = SentLog(path, clock=clock)
    log.write("made-up-old", None, "!r:made-up", 5, "sent")
    clock.now += KEEP_DAYS * 86400 + 1
    log.write("made-up-new", None, "!r:made-up", 5, "sent")
    SentLog(path, clock=clock).drop_unfinished()
    assert [line["id"] for line in _lines(path)] == ["made-up-new"]


def test_a_missing_log_is_an_empty_one(tmp_path):
    assert SentLog(tmp_path / "none" / "sent.jsonl", clock=_Clock()).drop_unfinished() == 0
