"""Drafts of messages the owner and the voice write together; nothing here sends anything.

messages-v1 §8: a draft lives only in this process's memory, one per recipient. Each change
gives it a new version, and it is dropped 15 minutes after its last change. A send (a later
slice) must name the version that was read back, so a changed draft always needs a new read-back.
Guard 3 lives here too: what a voice-written text may hold.
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Seconds a draft lives after its last change.
EXPIRY = 15 * 60
#: The most characters a voice-written text may have (v3.1 §8, guard 3).
MOST = 1000

STALE = "it changed; read it back first"
EXPIRED = "that draft expired"
UNKNOWN = "there is no such draft"

#: A web or mail address in any of its usual forms.
LINK = re.compile(r"(?i)(https?://|www\.|@|\b[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}\b)")
#: Six or more digits, with at most one space, dot or dash between each.
DIGITS = re.compile(r"\d(?:[\s.\-]?\d){5,}")


@dataclass(frozen=True)
class Draft:
    """One draft: its token and version, who it goes to, and the text read back to the owner."""

    token: str
    version: int
    #: The bridge's login it is sent from, and the room it goes to; both private.
    receiver: str
    room: str
    name: str
    text: str
    changed: float


class Drafts:
    """The drafts held in memory, one per recipient; a restart drops them all."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Hold no drafts yet; `clock` gives seconds, and only its differences matter."""
        self._clock = clock
        self._by_token: dict[str, Draft] = {}

    def write(self, receiver: str, room: str, name: str, text: str) -> tuple[Draft, bool]:
        """Create the draft to `room`, or change the one there is; and whether one was replaced."""
        now = self._clock()
        old = next((d for d in self._by_token.values() if (d.receiver, d.room) == (receiver, room)), None)
        if old is not None and self._expired(old, now):
            del self._by_token[old.token]
            old = None
        if old is None:
            draft = Draft(secrets.token_urlsafe(16), 1, receiver, room, name, text, now)
        else:
            # RULE: each change to a draft gives a new version
            draft = replace(old, version=old.version + 1, name=name, text=text, changed=now)
        self._by_token[draft.token] = draft
        return draft, old is not None

    def check(self, token: str, version: int) -> Draft | str:
        """The draft `token` names, if `version` is its latest; else why not, in words to say."""
        draft = self._by_token.get(token)
        if draft is None:
            return UNKNOWN
        if self._expired(draft, self._clock()):
            del self._by_token[token]
            return EXPIRED
        # RULE: a stale version is refused
        if version != draft.version:
            return STALE
        return draft

    @staticmethod
    def _expired(draft: Draft, now: float) -> bool:
        # RULE: a draft expires fifteen minutes after its last change
        return now - draft.changed > EXPIRY


def refused_text(text: str) -> str | None:
    """Why the voice may not send `text`, in words to say; None when it may (guard 3)."""
    if not text.strip():
        return "there is nothing to send"
    # RULE: a text over a thousand characters is refused
    if len(text) > MOST:
        return f"it is over {MOST} characters; send that from Element"
    # RULE: a text with a link is refused
    if LINK.search(text):
        return "it has a link; send that from Element"
    # RULE: a text with a long run of digits is refused
    if DIGITS.search(text):
        return "it has a long run of digits; send that from Element"
    return None
