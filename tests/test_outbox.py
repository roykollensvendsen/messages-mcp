"""The undo hold: confirm holds a draft for the delay, stop wins the race, and a restart sends nothing.

The sender here is a fake that only records; nothing is ever sent for real.
"""

import asyncio
import datetime as dt

import pytest

from messages_mcp.drafts import Drafts
from messages_mcp.fake import FakeSender
from messages_mcp.outbox import Outbox
from messages_mcp.send_limits import Limits
from messages_mcp.sent_log import SentLog

AT = dt.datetime(2026, 11, 18, 13, 2, tzinfo=dt.UTC)


def _outbox(tmp_path, *, delay=10.0, sender=None, limits=None, sleep=None):
    """An outbox whose sleep waits on an event the test sets, so the timer fires only when told."""
    drafts = Drafts(clock=lambda: 0.0)
    sender = sender or FakeSender()
    fire = asyncio.Event()

    async def wait(_seconds):
        await fire.wait()

    box = Outbox(
        drafts,
        sender,
        limits or Limits(clock=lambda: 0.0),
        SentLog(tmp_path / "sent.jsonl", clock=lambda: 1_800_000_000.0),
        delay=delay,
        now=lambda: AT,
        sleep=sleep or wait,
    )
    return box, drafts, sender, fire


def _draft(drafts, text="Made-up text", room="!made-up:made-up"):
    draft, _ = drafts.write("made-up-login-1", room, "Made-up Friend", text)
    return draft


def test_confirm_holds_the_message_and_says_how_to_stop_it(tmp_path):
    async def scenario():
        box, drafts, sender, _ = _outbox(tmp_path)
        draft = _draft(drafts)
        answer = await box.confirm(draft.token, draft.version)
        assert (answer["success"], answer["outcome"]) == (True, "held")
        assert answer["say"] == (
            "Sending to “Made-up Friend” in 10 seconds. Say stop to cancel. "
            "Call whats_new once after that to say whether it went."
        )
        assert sender.sent == []
        await box.close()

    asyncio.run(scenario())


def test_when_the_delay_runs_out_the_stored_text_is_sent(tmp_path):
    async def scenario():
        box, drafts, sender, fire = _outbox(tmp_path)
        draft = _draft(drafts, "Made-up stored text")
        await box.confirm(draft.token, draft.version)
        fire.set()
        await box.settle()
        assert sender.sent == [("made-up-login-1", "!made-up:made-up", "Made-up stored text")]
        assert box.news() == ["Sent to “Made-up Friend” at 13.02."]
        assert box.news() == []

    asyncio.run(scenario())


def test_stop_cancels_a_held_message(tmp_path):
    async def scenario():
        box, drafts, sender, fire = _outbox(tmp_path)
        draft = _draft(drafts)
        await box.confirm(draft.token, draft.version)
        answer = await box.cancel(draft.token)
        assert (answer["outcome"], answer["say"]) == ("cancelled", "Stopped. Nothing was sent to “Made-up Friend”.")
        fire.set()
        await box.settle()
        assert sender.sent == []
        assert box.news() == ["Stopped, not sent to “Made-up Friend”."]

    asyncio.run(scenario())


def test_a_cancel_after_the_send_is_too_late(tmp_path):
    async def scenario():
        box, drafts, _, fire = _outbox(tmp_path)
        draft = _draft(drafts)
        await box.confirm(draft.token, draft.version)
        fire.set()
        await box.settle()
        answer = await box.cancel(draft.token)
        assert (answer["outcome"], answer["say"]) == ("too_late", "Too late, it was sent at 13.02.")

    asyncio.run(scenario())


def test_stop_wins_the_race_with_exactly_one_outcome_every_time(tmp_path):
    """messages-v1 §8: cancel and the timer together, many times; sent or cancelled, never both or neither."""

    async def once(i):
        box, drafts, sender, _ = _outbox(tmp_path / str(i), delay=0.0, sleep=asyncio.sleep)
        draft = _draft(drafts)
        await box.confirm(draft.token, draft.version)
        for _ in range(i % 3):
            await asyncio.sleep(0)
        cancel = await box.cancel(draft.token)
        await box.settle()
        sent = len(sender.sent)
        stopped = cancel["outcome"] == "cancelled"
        assert sent + stopped == 1, (i, sent, cancel)
        return stopped

    async def scenario():
        return [await once(i) for i in range(300)]

    outcomes = asyncio.run(scenario())
    assert True in outcomes
    assert False in outcomes


def test_a_confirm_names_the_version_that_was_read_back(tmp_path):
    async def scenario():
        box, drafts, sender, _ = _outbox(tmp_path)
        draft = _draft(drafts, "Made-up one")
        _draft(drafts, "Made-up two")
        answer = await box.confirm(draft.token, 1)
        assert (answer["success"], answer["outcome"], answer["say"]) == (
            False,
            "refused",
            "Not sent: it changed; read it back first.",
        )
        assert sender.sent == []

    asyncio.run(scenario())


def test_a_draft_is_confirmed_once(tmp_path):
    async def scenario():
        box, drafts, _, _ = _outbox(tmp_path)
        draft = _draft(drafts)
        await box.confirm(draft.token, draft.version)
        again = await box.confirm(draft.token, draft.version)
        assert again["say"] == "Not sent: there is no such draft."
        await box.close()

    asyncio.run(scenario())


def test_past_a_limit_nothing_is_held(tmp_path):
    async def scenario():
        limits = Limits(clock=lambda: 0.0)
        for _ in range(3):
            limits.count("made-up-login-1", "!made-up:made-up")
        box, drafts, sender, _ = _outbox(tmp_path, limits=limits)
        draft = _draft(drafts)
        answer = await box.confirm(draft.token, draft.version)
        assert (answer["outcome"], answer["say"]) == (
            "refused",
            "Not sent: That's 3 to this chat in 10 minutes; the next can go in 10 minutes.",
        )
        assert sender.sent == []

    asyncio.run(scenario())


def test_a_send_that_fails_says_so_and_is_not_counted(tmp_path):
    async def scenario():
        limits = Limits(clock=lambda: 0.0)
        box, drafts, sender, fire = _outbox(tmp_path, sender=FakeSender(fails=True), limits=limits)
        draft = _draft(drafts)
        await box.confirm(draft.token, draft.version)
        fire.set()
        await box.settle()
        assert box.news() == ["The message to “Made-up Friend” did not go; it was not sent."]
        assert limits.refused("made-up-login-1", "!made-up:made-up") is None
        assert sender.sent == []

    asyncio.run(scenario())


def test_a_restart_during_the_hold_sends_nothing_and_says_so(tmp_path):
    """A held message lives only in memory: a stop of the holder cancels it, and it is never sent later."""

    async def before():
        box, drafts, sender, _ = _outbox(tmp_path)
        draft = _draft(drafts)
        await box.confirm(draft.token, draft.version)
        await box.close()  # what a stop of messages-mcp does to the timer
        return sender

    sender = asyncio.run(before())

    async def after():
        box, _, later, _ = _outbox(tmp_path)
        box.start()
        return box.news(), later

    news, later = asyncio.run(after())
    assert sender.sent == later.sent == []
    assert news == ["A held message was dropped when messages-mcp stopped; it was not sent."]


@pytest.mark.parametrize("outcome", ["held", "sent", "cancelled"])
def test_the_log_has_no_text_for_any_outcome(tmp_path, outcome):
    async def scenario():
        box, drafts, _, fire = _outbox(tmp_path)
        draft = _draft(drafts, "Made-up secret words")
        await box.confirm(draft.token, draft.version)
        if outcome == "cancelled":
            await box.cancel(draft.token)
        if outcome != "held":
            fire.set()
            await box.settle()
        await box.close()

    asyncio.run(scenario())
    log = (tmp_path / "sent.jsonl").read_text()
    assert "Made-up" not in log
    assert f'"outcome": "{outcome}"' in log


def test_a_stop_with_nothing_held_says_so(tmp_path):
    async def scenario():
        box, _, _, _ = _outbox(tmp_path)
        return await box.cancel("made-up-token")

    answer = asyncio.run(scenario())
    assert (answer["outcome"], answer["say"]) == ("not_found", "There is no message waiting to be sent.")


def test_a_second_stop_says_it_was_already_stopped(tmp_path):
    async def scenario():
        box, drafts, _, _ = _outbox(tmp_path)
        draft = _draft(drafts)
        await box.confirm(draft.token, draft.version)
        await box.cancel(draft.token)
        return await box.cancel(draft.token)

    answer = asyncio.run(scenario())
    assert (answer["outcome"], answer["say"]) == ("too_late", "It was already stopped; nothing was sent.")


def test_a_stop_after_a_failed_send_says_nothing_was_sent(tmp_path):
    async def scenario():
        box, drafts, _, fire = _outbox(tmp_path, sender=FakeSender(fails=True))
        draft = _draft(drafts)
        await box.confirm(draft.token, draft.version)
        fire.set()
        await box.settle()
        return await box.cancel(draft.token)

    answer = asyncio.run(scenario())
    assert answer["say"] == "It did not go; nothing was sent."


def test_a_stop_while_it_is_being_sent_is_too_late(tmp_path):
    class _Slow(FakeSender):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.finish = asyncio.Event()

        async def send(self, receiver, room, text):
            self.started.set()
            await self.finish.wait()
            await super().send(receiver, room, text)

    async def scenario():
        sender = _Slow()
        box, drafts, _, fire = _outbox(tmp_path, sender=sender)
        draft = _draft(drafts)
        await box.confirm(draft.token, draft.version)
        fire.set()
        await sender.started.wait()
        answer = await box.cancel(draft.token)
        sender.finish.set()
        await box.settle()
        return answer, sender.sent

    answer, sent = asyncio.run(scenario())
    assert answer["say"] == "Too late, it is being sent now."
    assert len(sent) == 1


def test_several_dropped_messages_are_counted_in_one_line(tmp_path):
    log = SentLog(tmp_path / "sent.jsonl", clock=lambda: 1_800_000_000.0)
    log.write("made-up-1", None, "!a:made-up", 3, "held")
    log.write("made-up-2", None, "!b:made-up", 3, "held")

    async def scenario():
        box, _, _, _ = _outbox(tmp_path)
        box.start()
        return box.news()

    assert asyncio.run(scenario()) == ["2 held messages were dropped when messages-mcp stopped; none was sent."]


def test_a_clean_start_has_no_news(tmp_path):
    async def scenario():
        box, _, _, _ = _outbox(tmp_path)
        box.start()
        return box.news()

    assert asyncio.run(scenario()) == []
