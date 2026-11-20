"""sms-bridge's core: pull the phone's texts through the hub, and post each into Matrix once.

The phone keeps new texts in a queue and never calls out (ADR-CVM-010). This pulls them with
the phone's one tool, `sms_since(cursor)`, whose answer has the shape in claude-voice-mobile's
`docs/plugins/sms/sms-since-answer.json`. There is no separate acknowledgement: passing an
answer's cursor back tells the phone it may delete what that answer held. So the cursor is kept
only once every text in its answer is posted, and a text posted before a failure is not posted
again, since the phone gives it again with the same id.

It waits 15 s after traffic, and twice as long after each empty poll, up to 2 minutes. An empty
poll is counted, never logged. A log line holds counts only: never a number, a name or a word of
a text, which are other people's and untrusted.

It knows the hub and the rooms only through `Hub` and `Rooms`, so the tests run on fakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn, Protocol

from messages_mcp.bridge_config import write_private

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

#: Seconds to wait after a poll that brought texts, and the most to wait when the phone is idle.
FIRST_WAIT = 15.0
LONGEST_WAIT = 120.0
#: Seconds to wait when the phone says more is waiting but gave nothing new, so it never spins.
MORE_BUT_NOTHING_NEW = 1.0
#: The most messages one answer may hold, as the schema says.
MOST_PER_ANSWER = 50
#: The words for the owner when texts come through again after an outage the owner was told of.
BACK = "Text messages from the phone are coming through again."
#: How many posted ids are remembered: the phone keeps at most 500, so twice that drops every repeat.
KEEP_SEEN = 1000
SENDER_KINDS = frozenset({"number", "short_code", "name"})
ANSWER_KEYS = frozenset({"messages", "cursor", "more", "dropped", "reset"})
MESSAGE_KEYS = frozenset({"id", "from", "from_kind", "received_at", "text", "cut", "masked", "untrusted"})
#: The schema's pattern for `received_at`: a time with its own offset, never a bare "Z".
RECEIVED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?[+-][0-9]{2}:[0-9]{2}")
#: The longest text the phone gives; a longer one is cut, and says so.
LONGEST_TEXT = 2000


class HubError(Exception):
    """Asking the phone through the hub failed."""


class RefusedError(HubError):
    """The phone refused, in its own words, as when its read permission was taken back."""


class BadAnswerError(HubError):
    """The phone's answer was not of the agreed shape, so nothing in it is used."""


@dataclass(frozen=True)
class Sms:
    """One text: who sent it, when, and its words, or the phone's line for a held-back code."""

    ident: str
    sender: str
    #: "number" (E.164), "short_code" or "name".
    sender_kind: str
    received_at: dt.datetime
    text: str
    masked: bool
    #: The text was longer than 2000 characters, and only its start is given.
    cut: bool = False


@dataclass(frozen=True)
class Answer:
    """One `sms_since` answer, checked."""

    messages: tuple[Sms, ...]
    cursor: str
    more: bool
    dropped: int
    reset: bool


class Hub(Protocol):
    """The hub's call to the phone's `sms_since` tool; it gives the answer's structured content."""

    async def sms_since(self, cursor: str | None) -> Any:  # noqa: ANN401 - checked by `parse`
        """The phone's answer for `cursor`, or None at the first call."""


class Rooms(Protocol):
    """The Matrix side: one room per sender, and the bridge's own room for notices."""

    async def post(self, sms: Sms) -> None:
        """Post `sms` into its sender's room."""

    async def notice(self, words: str) -> None:
        """Post `words` from the bridge itself."""


def parse(given: Any) -> Answer:  # noqa: ANN401 - this is where it is checked
    """`given` as an `Answer`, or `BadAnswerError` for anything off the schema."""
    if not isinstance(given, dict) or set(given) != ANSWER_KEYS:
        _refuse("the answer's fields are not the agreed ones")
    messages, cursor, dropped = given["messages"], given["cursor"], given["dropped"]
    if not isinstance(messages, list) or len(messages) > MOST_PER_ANSWER:
        _refuse("the answer's messages are not a list of at most 50")
    if not _text(cursor) or not isinstance(dropped, int) or isinstance(dropped, bool) or dropped < 0:
        _refuse("the answer's cursor or dropped count is off")
    if not isinstance(given["more"], bool) or not isinstance(given["reset"], bool):
        _refuse("the answer's more or reset is not true or false")
    return Answer(tuple(_sms(m) for m in messages), cursor, given["more"], dropped, given["reset"])


def _refuse(why: str, cause: Exception | None = None) -> NoReturn:
    raise BadAnswerError(why) from cause


def _text(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _sms(given: Any) -> Sms:  # noqa: ANN401 - this is where it is checked
    """One message, checked; it must be marked untrusted, as the schema's const says."""
    if not isinstance(given, dict) or set(given) != MESSAGE_KEYS or given["untrusted"] is not True:
        _refuse("a message's fields are not the agreed ones")
    if not (_text(given["id"]) and _text(given["from"])) or given["from_kind"] not in SENDER_KINDS:
        _refuse("a message's id or sender is off")
    if not isinstance(given["text"], str) or len(given["text"]) > LONGEST_TEXT:
        _refuse("a message's text is not a string of at most 2000")
    if not isinstance(given["cut"], bool) or not isinstance(given["masked"], bool):
        _refuse("a message's cut or mask is off")
    stamp = given["received_at"]
    if not isinstance(stamp, str) or not RECEIVED_AT.fullmatch(stamp):
        _refuse("a message's time is not ISO 8601 with an offset")
    try:
        at = dt.datetime.fromisoformat(stamp)
    except ValueError as impossible:
        _refuse("a message's time is not a real time", impossible)
    return Sms(given["id"], given["from"], given["from_kind"], at, given["text"], given["masked"], given["cut"])


def lost_notice(dropped: int) -> str:
    """The bridge's words for texts the phone dropped before the laptop had them.

    The phone's count can be a little high, never low (sms-since-answer.json at mobile #137): a
    text given in an answer that was not yet acknowledged may be counted too. So it is "up to".
    """
    texts = "text message" if dropped == 1 else "text messages"
    return (
        f"The phone dropped up to {dropped} {texts} before the laptop had them: more than 500 were"
        " waiting, they were older than 7 days, or its queue's key was lost. Read them on the phone."
    )


class SmsBridge:
    """Polls `hub`, posts to `rooms`, and keeps its cursor and the posted ids in `position`."""

    def __init__(self, hub: Hub, rooms: Rooms, position: pathlib.Path) -> None:
        """Start from the position kept in `position`, or from the phone's oldest text."""
        self.hub = hub
        self.rooms = rooms
        self.position = position
        self.keep_seen = KEEP_SEEN
        self.empty_polls = 0
        self._wait = FIRST_WAIT
        self._failed = 0
        self._told = False
        self._cursor, self._seen = self._load()

    def _load(self) -> tuple[str | None, list[str]]:
        try:
            kept = json.loads(self.position.read_text(encoding="utf-8"))
            cursor, seen = kept["cursor"], kept["seen"]
        except FileNotFoundError:
            return None, []
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("the kept position could not be read, so the bridge starts over from the phone's oldest text")
            return None, []
        return (cursor if _text(cursor) else None), [s for s in seen if isinstance(s, str)]

    def _save(self, cursor: str | None) -> None:
        self._seen = self._seen[-self.keep_seen :]
        self.position.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private(self.position, json.dumps({"cursor": cursor, "seen": self._seen}) + "\n")
        self._cursor = cursor

    def _longer(self) -> float:
        # RULE: each empty poll waits twice as long up to two minutes
        self._wait = min(self._wait * 2, LONGEST_WAIT)
        return self._wait

    async def poll(self) -> float:
        """Ask the phone once and post what is new; how many seconds to wait before the next poll."""
        try:
            got = parse(await self.hub.sms_since(self._cursor))
            posted = await self._post(got)
        except Exception as failed:  # noqa: BLE001 - every failure backs off the same way
            self._failed += 1
            # RULE: a phone that stays away is logged once
            if self._failed == 1:
                log.warning("the phone did not answer the poll, or its answer could not be posted: %s", _why(failed))
            await self._tell_outage(failed)
            return self._longer()
        if self._failed:
            log.info("the phone answers again, after %d failed polls", self._failed)
            self._failed = 0
        # RULE: the owner is told when texts come through again
        if self._told:
            self._told = not await self._tell(BACK)
        # RULE: an empty poll is counted never logged
        if posted or got.dropped or got.reset:
            log.info("posted %d new texts, %d lost on the phone", posted, got.dropped)
        else:
            self.empty_polls += 1
        # RULE: a poll with more waiting asks again at once
        if got.more:
            # RULE: a phone that says more with nothing new is asked again after a second
            return 0.0 if posted else MORE_BUT_NOTHING_NEW
        if posted:
            self._wait = FIRST_WAIT
            return self._wait
        return self._longer()

    async def _tell_outage(self, failed: Exception) -> None:
        """Tell the owner, once per outage, when the phone refuses or answers off the schema."""
        words = outage_notice(failed)
        # RULE: an outage the phone explains is told to the owner once
        if words is None or self._told:
            return
        self._told = await self._tell(words)

    async def _tell(self, words: str) -> bool:
        """Post `words` to the bridge's room; whether it was posted."""
        try:
            await self.rooms.notice(words)
        except Exception as failed:  # noqa: BLE001 - a notice that fails is tried again next outage
            log.warning("a notice to the bridge's room could not be posted: %s", type(failed).__name__)
            return False
        return True

    async def _post(self, got: Answer) -> int:
        """Post each new text in `got`, then its lost count; keep its cursor last. How many were posted."""
        if got.reset:
            log.info("the phone did not know the cursor, so it starts again from its oldest text")
        # RULE: a text the bridge has posted is never posted again
        new = [sms for sms in got.messages if sms.ident not in self._seen]
        for sms in new:
            await self.rooms.post(sms)
            self._seen.append(sms.ident)
            # RULE: the cursor is kept only once every text in its answer is posted
            self._save(self._cursor)
        # RULE: texts the phone lost are told in a notice
        if got.dropped:
            await self.rooms.notice(lost_notice(got.dropped))
        self._save(got.cursor)
        return len(new)

    async def run(self, stop: asyncio.Event, sleep: Callable[[float], Awaitable[object]] | None = None) -> None:
        """Poll until `stop` is set, waiting between polls as each one says; a stop ends a wait at once."""
        while not stop.is_set():
            wait = await self.poll()
            await (sleep(wait) if sleep is not None else _until(stop, wait))


def outage_notice(failed: Exception) -> str | None:
    """The owner's words for an outage the phone explains; None when the phone is only away."""
    if isinstance(failed, RefusedError):
        return f"Text messages have stopped: the phone refused sms-bridge. It said: {failed}"
    if isinstance(failed, BadAnswerError):
        return (
            "Text messages have stopped: the phone's answer to sms-bridge is not of the agreed shape"
            f" ({failed}). The texts wait on the phone; update claude-voice-mobile or messages-mcp so they agree."
        )
    return None


async def _until(stop: asyncio.Event, seconds: float) -> None:
    """Wait `seconds`, or less if `stop` is set meanwhile."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), seconds)


def _why(failed: Exception) -> str:
    """What went wrong, in the phone's or the code's words: never a text, which is never in an error."""
    if isinstance(failed, RefusedError | BadAnswerError):
        return str(failed)
    return type(failed).__name__
