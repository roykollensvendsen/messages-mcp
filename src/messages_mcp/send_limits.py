"""The rate limits on what the voice sends, per account, in code (v3.1 §8).

10 sends an hour and 30 a day in all, and at most 3 to one recipient in 10 minutes. A held
message counts at once; one that is stopped, dropped or fails is taken back off.
Past a limit, the answer says when it opens again.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

HOUR = 3600
DAY = 24 * HOUR
TEN_MINUTES = 600
#: (how many, in how many seconds, the words for the window, whether it is per recipient)
LIMITS = (
    (3, TEN_MINUTES, "to this chat in 10 minutes", True),
    (10, HOUR, "sends in an hour", False),
    (30, DAY, "sends today", False),
)


class Limits:
    """The sends counted so far, in memory, by account and room."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Count nothing yet; `clock` gives seconds."""
        self._clock = clock
        self._sends: list[tuple[float, str, str]] = []

    def refused(self, receiver: str, room: str) -> str | None:
        """Why one more send from `receiver` to `room` must wait, in words to say; None when it may go."""
        now = self._clock()
        self._sends = [s for s in self._sends if now - s[0] < DAY]
        for most, window, words, per_room in LIMITS:
            within = [
                at for at, r, m in self._sends if r == receiver and (m == room or not per_room) and now - at < window
            ]
            # RULE: three to one recipient in ten minutes then a wait
            if len(within) >= most:
                minutes = math.ceil((min(within) + window - now) / 60)
                return f"That's {most} {words}; the next can go in {minutes} minutes."
        return None

    def count(self, receiver: str, room: str) -> None:
        """Count one send from `receiver` to `room`, now."""
        self._sends.append((self._clock(), receiver, room))

    def uncount(self, receiver: str, room: str) -> None:
        """Take back the latest send from `receiver` to `room`: it did not go."""
        for i in range(len(self._sends) - 1, -1, -1):
            if self._sends[i][1:] == (receiver, room):
                del self._sends[i]
                return
