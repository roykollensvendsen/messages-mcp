"""What the plugin needs from the inbox, and the ways asking it can fail.

The service talks to this and nothing else. The fake (`fake.py`) and the Matrix adapter
(`matrix.py`) both answer it, so every test runs through the same door the real inbox does.
Everything in a `Chat` or a `Message` comes from other people: names and text alike are
untrusted, and none of it is ever logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import datetime as dt


class InboxError(Exception):
    """Asking the inbox failed; each kind below is said to the voice in its own words."""


class UnreachableError(InboxError):
    """The inbox did not answer in time."""


class NotSetUpError(InboxError):
    """The plugin has no login to the inbox yet, so nothing was asked."""


@dataclass(frozen=True)
class Message:
    """One message: who sent it, when, and its text, or what kind of thing it is."""

    sender: str
    at: dt.datetime
    body: str = ""
    #: "text", or what was sent instead: "picture", "voice message", "video", "file" or "other".
    kind: str = "text"
    #: Sent by the owner, from Element: kept for the record, never read back as news.
    own: bool = False


@dataclass(frozen=True)
class Chat:
    """One bridged chat, with its newest messages, oldest first."""

    name: str
    #: The bridge's login the chat belongs to; private, so it never leaves the plugin.
    receiver: str
    #: How many messages are unread, as the server counts them after the owner's own receipt.
    unread: int
    last_at: dt.datetime | None
    messages: tuple[Message, ...] = field(default_factory=tuple)
    #: The room a reply would go to; private, so it never leaves the plugin.
    room: str = ""
    #: A one-to-one chat, as the bridge marks it; a group is never written to by voice.
    direct: bool = False
    #: The owner has written in this chat before, in the history the inbox gave.
    owner_wrote: bool = False


class Inbox(Protocol):
    """An inbox the plugin can read, and only read."""

    async def chats(self) -> list[Chat]:
        """Every bridged chat, each with its unread count and newest messages."""
        ...
