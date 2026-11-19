"""The undo hold: a confirmed draft waits the delay, then goes, unless the owner says stop.

messages-v1 §8, pinned in code:
- confirm names the version that was read back, and a draft is confirmed once;
- the held message lives only in this process's memory, so a stop of messages-mcp cancels it,
  and the next start says a held message was dropped, never sending it later;
- a send already going when messages-mcp stops is waited for, for a while; one cut off is
  said at the next start as may or may not have gone, never as not sent;
- the timer and cancel take one lock, so each send ends in exactly one outcome;
- what goes is the stored draft's text, never anything from the confirm call.
Nothing here talks to Matrix: it sends through a `Sender`, and only a fake one exists so far.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from messages_mcp.drafts import Draft, Drafts
    from messages_mcp.send_limits import Limits
    from messages_mcp.sent_log import SentLog

DROPPED = "A held message was dropped when messages-mcp stopped; it was not sent."
UNKNOWN = "A message may or may not have gone when messages-mcp stopped; check the chat."
#: Seconds a stop of messages-mcp waits for a send already going before it cuts it off.
SEND_WAIT = 30.0


class Sender(Protocol):
    """Whatever finally sends a text into a room, from one of the bridge's logins."""

    async def send(self, receiver: str, room: str, text: str) -> None:
        """Send `text`; raise if it did not go."""
        ...


@dataclass
class Held:
    """One confirmed draft, and where it is: held, sending, sent, cancelled or failed."""

    ident: str
    draft: Draft
    state: str = "held"
    sent_at: str = ""
    task: asyncio.Task[None] | None = None


class Outbox:
    """The messages held for their undo delay, in memory only."""

    def __init__(  # noqa: PLR0913 - the outbox's own parts, each one swapped in tests
        self,
        drafts: Drafts,
        sender: Sender,
        limits: Limits,
        log: SentLog,
        *,
        delay: float,
        now: Callable[[], dt.datetime] | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        label: Callable[[str], str | None] = lambda _receiver: None,
        send_wait: float = SEND_WAIT,
    ) -> None:
        """Hold confirmed `drafts` for `delay` seconds, then send them with `sender`."""
        self._drafts = drafts
        self._sender = sender
        self._limits = limits
        self._log = log
        self._delay = delay
        self._now = now or (lambda: dt.datetime.now().astimezone())
        self._sleep = sleep
        self._label = label
        self._send_wait = send_wait
        self._lock = asyncio.Lock()
        self._held: dict[str, Held] = {}
        self._news: list[str] = []

    def start(self) -> None:
        """At start: say once what became of the sends messages-mcp was in the middle of when it stopped."""
        dropped, unknown = self._log.drop_unfinished()
        if dropped == 1:
            self._news.append(DROPPED)
        elif dropped:
            self._news.append(f"{dropped} held messages were dropped when messages-mcp stopped; none was sent.")
        if unknown == 1:
            self._news.append(UNKNOWN)
        elif unknown:
            self._news.append(
                f"{unknown} messages may or may not have gone when messages-mcp stopped; check the chats."
            )

    async def confirm(self, token: str, version: int) -> dict[str, Any]:
        """Hold the draft `token` names, if `version` is the one read back, for the undo delay."""
        found = self._drafts.check(token, version)
        if isinstance(found, str):
            return _refused(found)
        limited = self._limits.refused(found.receiver, found.room)
        if limited:
            return _refused(limited)
        # RULE: a draft is confirmed once
        self._drafts.take(token)
        self._limits.count(found.receiver, found.room)
        held = Held(secrets.token_hex(8), found)
        self._held[token] = held
        self._write(held, "held")
        held.task = asyncio.create_task(self._fire(held))
        seconds = f"{self._delay:g}"
        say = (
            f"Sending to “{found.name}” in {seconds} seconds. Say stop to cancel. "
            "Call whats_new once after that to say whether it went."
        )
        return {"success": True, "outcome": "held", "say": say, "token": token, "delay": self._delay}

    async def cancel(self, token: str) -> dict[str, Any]:
        """Stop the held message `token` names, if it has not gone yet."""
        held = self._held.get(token)
        if held is None:
            return {"success": False, "outcome": "not_found", "say": "There is no message waiting to be sent."}
        async with self._lock:
            # RULE: stop wins the race with exactly one outcome every time
            if held.state == "held":
                held.state = "cancelled"
                self._ended(held, "cancelled", f"Stopped, not sent to “{held.draft.name}”.")
                return {
                    "success": True,
                    "outcome": "cancelled",
                    "say": f"Stopped. Nothing was sent to “{held.draft.name}”.",
                }
        return {"success": False, "outcome": "too_late", "say": _late(held)}

    async def _fire(self, held: Held) -> None:
        await self._sleep(self._delay)
        async with self._lock:
            if held.state != "held":
                return
            held.state = "sending"
            # RULE: a message being sent when messages-mcp stops is never said to be unsent
            self._write(held, "sending")
        try:
            # RULE: when the delay runs out the stored text is sent
            await self._sender.send(held.draft.receiver, held.draft.room, held.draft.text)
        except Exception:  # noqa: BLE001 - whatever went wrong, the owner hears it did not go
            held.state = "failed"
            self._ended(held, "failed", f"The message to “{held.draft.name}” did not go; it was not sent.")
            return
        held.state = "sent"
        held.sent_at = self._now().strftime("%H.%M")
        self._write(held, "sent")
        self._news.append(f"Sent to “{held.draft.name}” at {held.sent_at}.")

    def _ended(self, held: Held, outcome: str, news: str) -> None:
        """A send that did not go: take it off the limits, log it, and say it."""
        self._limits.uncount(held.draft.receiver, held.draft.room)
        self._write(held, outcome)
        self._news.append(news)

    def _write(self, held: Held, outcome: str) -> None:
        draft = held.draft
        self._log.write(held.ident, self._label(draft.receiver), draft.room, len(draft.text), outcome)

    def news(self) -> list[str]:
        """What happened since the last call, in words to say; each item is said once."""
        news, self._news = self._news, []
        return news

    async def settle(self) -> None:
        """Wait until every held message has gone or stopped."""
        await asyncio.gather(*(h.task for h in self._held.values() if h.task), return_exceptions=True)

    async def close(self) -> None:
        """Stop every timer, as a stop of messages-mcp does: nothing held is sent.

        A send already going is waited for, up to the send wait, and only then cut off: its
        log then ends at sending, which the next start says may or may not have gone.
        """
        live = [h for h in self._held.values() if h.task and not h.task.done()]
        # RULE: a stop of messages-mcp waits for a send already going
        going = [h.task for h in live if h.state == "sending" and h.task]
        if going:
            await asyncio.wait(going, timeout=self._send_wait)
        tasks = [h.task for h in live if h.task and not h.task.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


def _refused(why: str) -> dict[str, Any]:
    return {"success": False, "outcome": "refused", "say": f"Not sent: {why.rstrip('.')}."}


def _late(held: Held) -> str:
    if held.state == "sent":
        return f"Too late, it was sent at {held.sent_at}."
    if held.state == "sending":
        return "Too late, it is being sent now."
    if held.state == "failed":
        return "It did not go; nothing was sent."
    return "It was already stopped; nothing was sent."
