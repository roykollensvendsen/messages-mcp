"""The inbox on the laptop's own Synapse, read with one sync and nothing else.

Wall 2 of three against "seen" reaching Messenger: this adapter asks Synapse one thing,
`GET /sync` with `set_presence=offline` and no wait, and never sends a receipt, a private
receipt, a typing notice, presence or a message. It has no method that could. Each read is
a full sync with no `since` token, so it carries no position between reads and always sees
each room's newest events and whole state.

Only the bridge's portal rooms count: a room is one when its `m.bridge` state names the
Messenger connector and was sent by the bridge's own bot, `@facebookbot:<the owner's server>`.
Our fragment sets that bot's name (`config/mautrix-meta/messages-mcp.yaml`), so a member who
sets an `m.bridge` state of their own makes no room a portal. The login a portal belongs to is
that state's `channel["fi.mau.receiver"]`.

Unread counts are Synapse's `unread_notifications`, which move only when the owner's own
Element sends a receipt. Element has public receipts off (owner step E), so that is the
private one, `m.read.private`, which Synapse counts and the bridge never passes on.

It logs how many chats it read and how long it took, never a name, a room or a word of text.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
import urllib.parse
from typing import TYPE_CHECKING, Any

from messages_mcp.device import Chat, Message, NotSetUpError, UnreachableError

if TYPE_CHECKING:
    import pathlib

log = logging.getLogger(__name__)

#: mautrix-meta's network ID, in `m.bridge`'s `protocol.id` (its connector's `GetName`).
PROTOCOL = "facebook"
#: The bridge bot's username, as our fragment sets it in `appservice.bot.username`.
BOT_USERNAME = "facebookbot"
BRIDGE = "m.bridge"
RECEIVER = "fi.mau.receiver"
#: How many of each room's newest events one sync brings; a read shows at most 10 of them.
TIMELINE = 20
#: The one request made: no presence, no account data, the newest events of each room.
FILTER = json.dumps(
    {
        "presence": {"not_types": ["*"]},
        "account_data": {"not_types": ["*"]},
        "room": {
            "timeline": {"limit": TIMELINE},
            "ephemeral": {"not_types": ["*"]},
            "account_data": {"not_types": ["*"]},
        },
    },
    separators=(",", ":"),
)
KINDS = {
    "m.text": "text",
    "m.notice": "text",
    "m.emote": "text",
    "m.image": "picture",
    "m.audio": "voice message",
    "m.video": "video",
    "m.file": "file",
}


class MatrixInbox:
    """The bridged chats on Synapse, read with the plugin's own login in `login`."""

    def __init__(self, login: pathlib.Path) -> None:
        """Read with the login `messages-mcp login` wrote; it is read again on every ask."""
        self.login = login

    async def chats(self) -> list[Chat]:
        """Every portal room, with its unread count and newest messages."""
        homeserver, token, owner = _login(self.login)
        bot = bot_of(owner)
        started = time.monotonic()
        # mautrix and aiohttp are slow imports, and only a real read needs them.
        from aiohttp import ClientSession  # noqa: PLC0415
        from mautrix.client import ClientAPI  # noqa: PLC0415
        from mautrix.types import FilterID, PresenceState  # noqa: PLC0415

        quiet = logging.getLogger("messages_mcp.mautrix")
        quiet.setLevel(logging.WARNING)
        async with ClientSession() as session:
            client = ClientAPI(base_url=homeserver, token=token, client_session=session, log=quiet)
            try:
                # RULE: the plugin asks synapse only for a sync and sends nothing
                synced: dict[str, Any] = await client.sync(
                    timeout=0,
                    filter_id=FilterID(FILTER),  # the spec takes a filter as JSON as well as an ID
                    full_state=True,
                    set_presence=PresenceState.OFFLINE,
                )
            except Exception as failed:
                msg = "the inbox did not answer"
                raise UnreachableError(msg) from failed
        rooms: dict[str, Any] = (synced or {}).get("rooms", {}).get("join", {})
        chats = [chat for room in rooms.values() if (chat := _chat(room, owner, bot)) is not None]
        log.info("read %d bridged chats of %d rooms in %.2f s", len(chats), len(rooms), time.monotonic() - started)
        return chats


#: The names of this laptop: the token can send as the owner, so it goes to no other host.
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def on_this_laptop(homeserver: str) -> bool:
    """Whether `homeserver` is reached on this laptop's loopback, never across a network."""
    # RULE: the plugin talks only to a synapse on this laptop
    return urllib.parse.urlsplit(homeserver).hostname in LOOPBACK


def _login(path: pathlib.Path) -> tuple[str, str, str]:
    """The homeserver, token and owner's user ID in `path`, or why the plugin is not set up."""
    try:
        login = json.loads(path.read_text(encoding="utf-8"))
        homeserver, token = str(login["homeserver"]), str(login["access_token"])
    except (OSError, ValueError, KeyError, TypeError) as missing:
        msg = f"no usable login in {path}; run: messages-mcp login"
        raise NotSetUpError(msg) from missing
    if not on_this_laptop(homeserver):
        msg = f"the login in {path} is for a server that is not on this laptop; run: messages-mcp login"
        raise NotSetUpError(msg)
    owner = str(login.get("user_id", ""))
    if ":" not in owner:
        msg = f"the login in {path} names no Matrix user; run: messages-mcp login"
        raise NotSetUpError(msg)
    return homeserver, token, owner


def bot_of(owner: str) -> str:
    """The bridge bot's Matrix ID on the owner's own server."""
    return f"@{BOT_USERNAME}:{owner.split(':', 1)[1]}"


def _events(room: dict[str, Any], part: str) -> list[dict[str, Any]]:
    events = room.get(part, {}).get("events", [])
    return [e for e in events if isinstance(e, dict)]


def _chat(room: dict[str, Any], owner: str, bot: str) -> Chat | None:
    """The chat in one synced room, or None when it is not a Messenger portal."""
    timeline = _events(room, "timeline")
    events = [*_events(room, "state"), *timeline]
    state = _state(events)
    bridge = _bridge(events, bot)
    if bridge is None:
        return None
    channel = bridge.get("channel") or {}
    names = {key: str(c.get("displayname") or "") for (kind, key), c in state.items() if kind == "m.room.member"}
    messages = tuple(m for e in timeline if (m := _message(e, names, owner)) is not None)
    unread = int((room.get("unread_notifications") or {}).get("notification_count") or 0)
    name = str((state.get(("m.room.name", "")) or {}).get("name") or channel.get("displayname") or "a chat")
    return Chat(
        name=name,
        receiver=str(channel.get(RECEIVER) or ""),
        unread=unread,
        last_at=messages[-1].at if messages else None,
        messages=messages,
    )


def _state(events: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """The room's state, by type and key, the latest of each winning."""
    return {(e.get("type", ""), e["state_key"]): e.get("content") or {} for e in events if "state_key" in e}


def _bridge(events: list[dict[str, Any]], bot: str) -> dict[str, Any] | None:
    """The `m.bridge` content the bridge's bot set, when the room is a Messenger portal; else None."""
    # RULE: only the bridge bot can make a room a portal
    bridge: dict[str, Any] | None = next((e.get("content") for e in reversed(events) if _from_bot(e, bot)), None)
    # RULE: only the bridges messenger rooms are read
    if not isinstance(bridge, dict) or (bridge.get("protocol") or {}).get("id") != PROTOCOL:
        return None
    return bridge


def _from_bot(event: dict[str, Any], bot: str) -> bool:
    return event.get("type") == BRIDGE and "state_key" in event and event.get("sender") == bot


def _message(event: dict[str, Any], names: dict[str, str], owner: str) -> Message | None:
    """One message event as a `Message`; None for anything that is not one, or for an edit."""
    kind = event.get("type")
    content = event.get("content") or {}
    if kind not in {"m.room.message", "m.room.encrypted"} or "state_key" in event or _edit(content):
        return None
    sender = str(event.get("sender", ""))
    own = bool(owner) and sender == owner
    who = names.get(sender) or sender.removeprefix("@").split(":")[0] or "someone"
    at = dt.datetime.fromtimestamp(int(event.get("origin_server_ts", 0)) / 1000, tz=dt.UTC)
    if kind == "m.room.encrypted":
        return Message(who, at, kind="encrypted message", own=own)
    sort = KINDS.get(str(content.get("msgtype")), "other")
    body = str(content.get("body", "")) if sort == "text" else ""
    return Message(who, at, body, sort, own=own)


def _edit(content: dict[str, Any]) -> bool:
    """Whether `content` changes an earlier message: the earlier one already says what was sent."""
    return (content.get("m.relates_to") or {}).get("rel_type") == "m.replace"
