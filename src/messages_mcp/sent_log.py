"""sent.jsonl: what the voice sent, with no text and no names (v3.1 §8).

One line per step: when, an id for the send, the account's label, a hash of the room, the
text's length, and the outcome (held, sent, cancelled, failed or dropped). It answers "did it
go?" and "how many today?" without holding the content. Kept 90 days, readable by the owner only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from typing import TYPE_CHECKING

from messages_mcp.bridge_config import write_private

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Callable

KEEP_DAYS = 90
#: The outcomes that end a send; a held one with none of these after it was dropped.
FINAL = {"sent", "cancelled", "failed", "dropped"}


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

    def drop_unfinished(self) -> int:
        """At start: mark every held send with no outcome as dropped, keep 90 days; how many were dropped."""
        try:
            lines = [json.loads(text) for text in self.path.read_text(encoding="utf-8").splitlines() if text]
        except FileNotFoundError:
            return 0
        oldest = dt.datetime.fromtimestamp(self._clock(), dt.UTC) - dt.timedelta(days=KEEP_DAYS)
        # RULE: lines older than ninety days are dropped
        kept = [line for line in lines if dt.datetime.fromisoformat(line["at"]) >= oldest]
        ended = {line["id"] for line in kept if line["outcome"] in FINAL}
        held = [line for line in kept if line["outcome"] == "held" and line["id"] not in ended]
        at = dt.datetime.fromtimestamp(self._clock(), dt.UTC).isoformat(timespec="seconds")
        kept += [{**line, "at": at, "outcome": "dropped"} for line in held]
        write_private(self.path, "".join(json.dumps(line) + "\n" for line in kept))
        return len(held)
