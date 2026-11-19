"""sent.jsonl: what the voice sent, with no text and no names (v3.1 §8).

One line per step: when, an id for the send, the account's label, a hash of the room, the
text's length, and the outcome (held, sending, sent, cancelled, failed, dropped or unknown). It answers "did it
go?" and "how many today?" without holding the content. Kept 90 days, readable by the owner only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from typing import TYPE_CHECKING, Any

from messages_mcp.bridge_config import write_private

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Callable

KEEP_DAYS = 90
#: The outcomes that end a send. A held one with none of these after it was dropped; one
#: that had started sending may have gone, so it is unknown.
FINAL = {"sent", "cancelled", "failed", "dropped", "unknown"}


class SentLog:
    """The log at `path`, created at 0600 in a 0700 folder."""

    def __init__(self, path: pathlib.Path, clock: Callable[[], float] = time.time) -> None:
        """Write to `path`; `clock` gives seconds since the epoch."""
        self.path = path
        self._clock = clock

    def write(self, ident: str, account: str | None, room: str, length: int, outcome: str) -> None:
        """Add one line; the room is hashed and no text is ever passed in."""
        line = {
            "at": dt.datetime.fromtimestamp(self._clock(), dt.UTC).isoformat(timespec="seconds"),
            "id": ident,
            "account": account,
            # RULE: a line holds no text and no room id
            "room": hashlib.sha256(room.encode()).hexdigest()[:16],
            "length": length,
            "outcome": outcome,
        }
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as out:
            out.write(json.dumps(line) + "\n")

    def drop_unfinished(self) -> tuple[int, int]:
        """At start: end every send with no outcome, keep 90 days; how many were dropped, and how many unknown.

        A send that never started was dropped. One that had started may have reached the server,
        so it is unknown: saying it was not sent could be false.
        """
        try:
            lines = [json.loads(text) for text in self.path.read_text(encoding="utf-8").splitlines() if text]
        except FileNotFoundError:
            return 0, 0
        oldest = dt.datetime.fromtimestamp(self._clock(), dt.UTC) - dt.timedelta(days=KEEP_DAYS)
        # RULE: lines older than ninety days are dropped
        kept = [line for line in lines if dt.datetime.fromisoformat(line["at"]) >= oldest]
        held, started = _unfinished(kept)
        at = dt.datetime.fromtimestamp(self._clock(), dt.UTC).isoformat(timespec="seconds")
        kept += [{**line, "at": at, "outcome": "dropped"} for line in held]
        # RULE: a send cut off mid way is unknown never dropped
        kept += [{**line, "at": at, "outcome": "unknown"} for line in started.values()]
        write_private(self.path, "".join(json.dumps(line) + "\n" for line in kept))
        return len(held), len(started)


def _unfinished(lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """The sends with no outcome: those still held, and those that had started sending, by id."""
    ended = {line["id"] for line in lines if line["outcome"] in FINAL}
    started = {line["id"]: line for line in lines if line["outcome"] == "sending" and line["id"] not in ended}
    held = [line for line in lines if line["outcome"] == "held" and line["id"] not in ended | set(started)]
    return held, started
