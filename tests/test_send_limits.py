"""The rate limits, per account: 10 an hour, 30 a day, and 3 to one recipient in 10 minutes."""

from messages_mcp.send_limits import Limits


class _Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def _limits():
    clock = _Clock()
    return Limits(clock=clock), clock


def _send(limits, clock, n, *, rooms=None, every=1.0):
    for i in range(n):
        room = rooms[i % len(rooms)] if rooms else f"!made-up-{i}:made-up"
        assert limits.refused("made-up-login-1", room) is None
        limits.count("made-up-login-1", room)
        clock.now += every


def test_three_to_one_recipient_in_ten_minutes_then_a_wait():
    limits, clock = _limits()
    _send(limits, clock, 3, rooms=["!one:made-up"])
    assert limits.refused("made-up-login-1", "!one:made-up") == (
        "That's 3 to this chat in 10 minutes; the next can go in 10 minutes."
    )
    assert limits.refused("made-up-login-1", "!other:made-up") is None
    clock.now += 600
    assert limits.refused("made-up-login-1", "!one:made-up") is None


def test_ten_an_hour_in_all():
    limits, clock = _limits()
    _send(limits, clock, 10, every=60)
    assert (
        limits.refused("made-up-login-1", "!new:made-up")
        == "That's 10 sends in an hour; the next can go in 50 minutes."
    )
    clock.now += 50 * 60
    assert limits.refused("made-up-login-1", "!new:made-up") is None


def test_thirty_a_day_in_all():
    limits, clock = _limits()
    for _ in range(3):
        _send(limits, clock, 10, every=60)
        clock.now += 3600
    assert limits.refused("made-up-login-1", "!new:made-up").startswith("That's 30 sends today; the next can go in ")


def test_the_limits_are_counted_per_account():
    limits, clock = _limits()
    _send(limits, clock, 10)
    assert limits.refused("made-up-login-1", "!new:made-up") is not None
    assert limits.refused("made-up-login-2", "!new:made-up") is None


def test_a_send_that_did_not_go_is_not_counted():
    limits, clock = _limits()
    _send(limits, clock, 3, rooms=["!one:made-up"])
    limits.uncount("made-up-login-1", "!one:made-up")
    assert limits.refused("made-up-login-1", "!one:made-up") is None


def test_taking_back_a_send_that_was_never_counted_changes_nothing():
    limits, clock = _limits()
    _send(limits, clock, 3, rooms=["!one:made-up"])
    limits.uncount("made-up-login-1", "!never:made-up")
    assert limits.refused("made-up-login-1", "!one:made-up") is not None
