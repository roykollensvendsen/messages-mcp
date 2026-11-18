"""The core: read the inbox, and word every answer for the voice.

It knows no MCP. Every answer is a dict with `success`, an `outcome` and a `say`, the
sentence the voice reads aloud as it is, and it has one even when the inbox fails.

Everything other people wrote, their names included, comes back inside `quoted_text`,
`from` and `chat`, with `untrusted: true`. The `say` line quotes it and names who wrote it,
so the voice reads it as "X wrote: ...", never as words of the owner's or of this plugin.
Nothing here marks a message read, and nothing here logs a name or a word of text.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import TYPE_CHECKING, Any

from messages_mcp.device import NotSetUpError, UnreachableError

if TYPE_CHECKING:
    from collections.abc import Callable

    from messages_mcp.accounts import Accounts
    from messages_mcp.device import Chat, Inbox, Message

#: v2.1 §2: the latest message per chat for "anything new?", and each message in a read.
UNREAD_CUT = 200
READ_CUT = 300
#: At most this many messages in one read.
READ_MOST = 10
#: messages_chats lists the chats with activity in this many days.
ACTIVE_DAYS = 7
#: Names other people chose, of chats and of people, are cut to this many characters.
NAME_CUT = 60
#: Every quote mark someone else could use to end the quote the voice reads them in: “ ” " « » „ and the single ones.
QUOTES = str.maketrans(dict.fromkeys('\u201c\u201d"\u00ab\u00bb\u201e\u2039\u203a\u201a\u2018\u2019`', "'"))

UNREACHABLE = "The message inbox isn't answering. Check that Synapse is running on the laptop."
NOT_SET_UP = "messages-mcp isn't logged in to the inbox yet, so nothing was read."
BROKE = "That went wrong inside messages-mcp. Try again, and if it fails again, check its log."


def cut(text: str, most: int) -> tuple[str, bool]:
    """`text` cut to `most` characters, and whether it was cut."""
    # RULE: a message is cut to its limit and says so
    if len(text) <= most:
        return text, False
    return text[:most].rstrip() + "…", True


def clean(text: str) -> str:
    """Someone else's text on one line, with no quote mark that could end its quote early."""
    # RULE: a quote mark from someone else cannot close the quote it is read in
    flat = " ".join(text.translate(QUOTES).split())
    return "".join(ch for ch in flat if ch.isprintable())


def _cleaned(chat: Chat) -> Chat:
    """`chat` as the voice may hear it: names cut, every text cleaned, the owner's own messages left out."""
    messages = tuple(
        dataclasses.replace(m, sender=cut(clean(m.sender), NAME_CUT)[0], body=clean(m.body))
        for m in chat.messages
        # RULE: the owners own messages are not read back as news
        if not m.own
    )
    return dataclasses.replace(chat, name=cut(clean(chat.name), NAME_CUT)[0], messages=messages)


def _spoken(message: Message, most: int) -> tuple[str, bool]:
    """What the voice may read of `message`: its cut text, or what kind of thing it is."""
    if message.kind != "text":
        article = "an" if message.kind[0] in "aeiou" else "a"
        return f"[{article} {message.kind}]", False
    return cut(message.body, most)


class MessagesService:
    """What the tools ask, answered from one inbox."""

    def __init__(self, inbox: Inbox, accounts: Accounts, now: Callable[[], dt.datetime] | None = None) -> None:
        """Answer from `inbox`, the fake or the Matrix one, labelling logins with `accounts`."""
        self._inbox = inbox
        self._accounts = accounts
        self._now = now or (lambda: dt.datetime.now(dt.UTC))

    async def _read(self, account: str | None) -> Read | dict[str, Any]:
        """The chats of `account`, or every chat, with their labels; or the failed answer instead."""
        try:
            chats = await self._inbox.chats()
        except UnreachableError:
            return _failed("unreachable", UNREACHABLE)
        except NotSetUpError:
            return _failed("not_set_up", NOT_SET_UP)
        # RULE: every answer has a sentence to say even when the inbox fails
        except Exception:  # noqa: BLE001 - whatever broke, the voice gets a sentence
            return _failed("broke", BROKE)
        labels, news = self._accounts.labels(_receivers(chats))
        return _of(account, chats, labels), labels, news

    async def unread(self, account: str | None = None) -> dict[str, Any]:
        """Who has written and how many, with each chat's latest message cut to 200."""
        read = await self._read(account)
        if isinstance(read, dict):
            return read
        chats, labels, news = read
        items, say = _unread(chats, labels)
        return _answered("unread", say, news, chats=items)

    async def read(self, chat: str, account: str | None = None) -> dict[str, Any]:
        """The latest unread messages in one chat, at most 10, each cut to 300; none marked read."""
        read = await self._read(account)
        if isinstance(read, dict):
            return read
        chats, _, news = read
        found = _find(chat, chats)
        if isinstance(found, str):
            return _failed("not_found", found)
        # RULE: a read gives at most ten of the unread messages
        unread = found.messages[-min(found.unread, READ_MOST) :] if found.unread else ()
        items = [_read_item(message) for message in unread]
        if not items:
            return _answered("read", f"No unread messages in “{found.name}”.", news, chat=found.name, messages=[])
        return _answered("read", _read_say(found.name, items), news, chat=found.name, messages=items)

    async def chats(self, account: str | None = None) -> dict[str, Any]:
        """The chats with activity in the last 7 days, newest first, with no message text."""
        read = await self._read(account)
        if isinstance(read, dict):
            return read
        chats, labels, news = read
        since = self._now() - dt.timedelta(days=ACTIVE_DAYS)
        active = sorted((c for c in chats if _newest(c) >= since), key=_newest, reverse=True)
        items = [_chat_item(c, labels[c.receiver]) for c in active]
        return _answered("chats", _chats_say(active), news, chats=items)


#: What a read of the inbox gives: the chats asked for, every login's label, and any news.
Read = tuple[list["Chat"], dict[str, str | None], str | None]
EARLIEST = dt.datetime.min.replace(tzinfo=dt.UTC)


def _newest(chat: Chat) -> dt.datetime:
    """When `chat` last had a message; the earliest time there is when it has none."""
    return chat.last_at or EARLIEST


def _when(at: dt.datetime) -> str:
    return at.isoformat(timespec="minutes")


def _unread_item(chat: Chat, label: str | None) -> tuple[dict[str, Any], str]:
    """One chat's entry in the unread answer, and its line for the voice."""
    latest = chat.messages[-1]
    text, was_cut = _spoken(latest, UNREAD_CUT)
    item = {
        "account": label,
        "chat": chat.name,
        "count": chat.unread,
        "when": _when(latest.at),
        "from": latest.sender,
        "quoted_text": text,
        "cut": was_cut,
        "untrusted": True,
    }
    where = f"{label}, " if label else ""
    return item, f"{where}“{chat.name}”: {chat.unread} new; “{latest.sender}” wrote: “{text}”."


def _receivers(chats: list[Chat]) -> list[str]:
    return [chat.receiver for chat in chats]


def _of(account: str | None, chats: list[Chat], labels: dict[str, str | None]) -> list[Chat]:
    """The chats of `account`, or every chat when none is asked for."""
    return [_cleaned(chat) for chat in chats if _in(labels, chat, account)]


def _unread(chats: list[Chat], labels: dict[str, str | None]) -> tuple[list[dict[str, Any]], str]:
    """The chats with something new, newest first, and the sentence that says them."""
    new = sorted(filter(_has_news, chats), key=_newest, reverse=True)
    items, lines = [], []
    for chat in new:
        item, line = _unread_item(chat, labels[chat.receiver])
        items.append(item)
        lines.append(line)
    return items, _unread_say(lines)


def _has_news(chat: Chat) -> bool:
    return chat.unread > 0 and bool(chat.messages)


def _read_say(name: str, items: list[dict[str, Any]]) -> str:
    said = " ".join(f"“{m['from']}” wrote: “{m['quoted_text']}”." for m in items)
    return f"“{name}”, {len(items)} unread. {said} They stay unread."


def _chats_say(active: list[Chat]) -> str:
    if not active:
        return "No chats this week."
    return f"{len(active)} chats this week: " + ", ".join(f"“{c.name}”" for c in active) + "."


def _unread_say(lines: list[str]) -> str:
    if not lines:
        return "Nothing new."
    chats = "1 chat" if len(lines) == 1 else f"{len(lines)} chats"
    return f"{chats} with new messages. " + " ".join(lines)


def _read_item(message: Message) -> dict[str, Any]:
    text, was_cut = _spoken(message, READ_CUT)
    return {"from": message.sender, "when": _when(message.at), "quoted_text": text, "cut": was_cut, "untrusted": True}


def _chat_item(chat: Chat, label: str | None) -> dict[str, Any]:
    return {"account": label, "chat": chat.name, "unread": chat.unread, "when": _when(_newest(chat)), "untrusted": True}


def _in(labels: dict[str, str | None], chat: Chat, account: str | None) -> bool:
    """Whether `chat` belongs to the `account` asked for; every chat when none is asked for."""
    return account is None or (labels.get(chat.receiver) or "").casefold() == account.casefold()


def _find(name: str, chats: list[Chat]) -> Chat | str:
    """The one chat called `name`, or else the one whose name holds it, or a sentence saying why not."""
    wanted = name.casefold().strip()
    near = _exactly(wanted, chats) or _holding(wanted, chats)
    if len(near) == 1:
        return near[0]
    if not near:
        return "No chat by that name."
    return "More than one chat matches: " + ", ".join(f"“{c.name}”" for c in near[:5]) + ". Which one?"


def _exactly(wanted: str, chats: list[Chat]) -> list[Chat]:
    return [c for c in chats if c.name.casefold() == wanted]


def _holding(wanted: str, chats: list[Chat]) -> list[Chat]:
    """The chats whose name holds `wanted`; none for an empty name."""
    return [c for c in chats if wanted and wanted in c.name.casefold()]


def _answered(outcome: str, say: str, news: str | None, **rest: Any) -> dict[str, Any]:  # noqa: ANN401
    """A successful answer, with the one-time news about a second account first."""
    return {"success": True, "outcome": outcome, "say": f"{news} {say}" if news else say, **rest}


def _failed(outcome: str, say: str) -> dict[str, Any]:
    return {"success": False, "outcome": outcome, "say": say}
