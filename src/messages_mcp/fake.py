"""A made-up inbox, with chats and people that do not exist.

It answers exactly what the Matrix adapter answers, so the service and the tools are tested
through the same door. It can be told to stop answering, as an inbox whose server is down.
"""

from __future__ import annotations

import datetime as dt

from messages_mcp.device import Chat, Message, UnreachableError

#: A fixed moment, so a simulated answer is the same every time.
NOW = dt.datetime(2026, 11, 18, 12, 0, tzinfo=dt.UTC)


def _ago(minutes: int) -> dt.datetime:
    return NOW - dt.timedelta(minutes=minutes)


#: The made-up chats `--simulate` reads: names, people and texts are all invented.
MADE_UP = (
    Chat(
        name="Made-up Friend",
        receiver="made-up-login-1",
        unread=2,
        last_at=_ago(5),
        messages=(
            Message("Made-up Friend", _ago(9), "Are you coming on Saturday?"),
            Message("Made-up Friend", _ago(5), "Bring the made-up board game."),
        ),
    ),
    Chat(
        name="Made-up Football Group",
        receiver="made-up-login-1",
        unread=1,
        last_at=_ago(30),
        messages=(Message("Made-up Coach", _ago(30), "Training moves to seven."),),
    ),
    Chat(
        name="Made-up Neighbour",
        receiver="made-up-login-1",
        unread=0,
        last_at=_ago(60 * 24 * 3),
        messages=(Message("Made-up Neighbour", _ago(60 * 24 * 3), "Thanks for the ladder."),),
    ),
)


class FakeInbox:
    """An inbox in memory, made of `chats`."""

    def __init__(self, chats: tuple[Chat, ...] | list[Chat] = MADE_UP) -> None:
        """Start with `chats`; the made-up ones by default."""
        self.chats_held = list(chats)
        self.unreachable = False
        self.asked = 0

    async def chats(self) -> list[Chat]:
        """The chats, unless the inbox is off."""
        self.asked += 1
        if self.unreachable:
            msg = "the inbox did not answer"
            raise UnreachableError(msg)
        return list(self.chats_held)
