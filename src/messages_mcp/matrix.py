"""The inbox on the laptop's own Synapse, read with one sync and nothing else.

Wall 2 of three against "seen" reaching Messenger: this adapter asks Synapse one thing,
`GET /sync` with `set_presence=offline` and no wait, and never sends a receipt, a private
receipt, a typing notice, presence or a message. It has no method that could. Only the
bridge's portal rooms count: a room is one when its `m.bridge` state names the Messenger
connector, and the login it belongs to is that state's `channel["fi.mau.receiver"]`.

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
from typing import TYPE_CHECKING, Any

from messages_mcp.device import Chat, Message, NotSetUpError, UnreachableError

if TYPE_CHECKING:
    import pathlib

log = logging.getLogger(__name__)

#: mautrix-meta's network ID, in `m.bridge`'s `protocol.id` (its connector's `GetName`).
PROTOCOL = "facebook"
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
        homeserver, token = _login(self.login)
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
        chats = [chat for room in rooms.values() if (chat := _chat(room)) is not None]
        log.info("read %d bridged chats of %d rooms in %.2f s", len(chats), len(rooms), time.monotonic() - started)
        return chats


def _login(path: pathlib.Path) -> tuple[str, str]:
    """The homeserver and token in `path`, or why the plugin is not set up."""
    try:
        login = json.loads(path.read_text(encoding="utf-8"))
        return str(login["homeserver"]), str(login["access_token"])
    except (OSError, ValueError, KeyError, TypeError) as missing:
        msg = f"no usable login in {path}; run: messages-mcp login"
        raise NotSetUpError(msg) from missing


def _events(room: dict[str, Any], part: str) -> list[dict[str, Any]]:
    events = room.get(part, {}).get("events", [])
    return [e for e in events if isinstance(e, dict)]


def _chat(room: dict[str, Any]) -> Chat | None:
    """The chat in one synced room, or None when it is not a Messenger portal."""
    timeline = _events(room, "timeline")
    state = _state([*_events(room, "state"), *timeline])
    bridge = _bridge(state)
    if bridge is None:
        return None
    channel = bridge.get("channel") or {}
    names = {key: str(c.get("displayname") or "") for (kind, key), c in state.items() if kind == "m.room.member"}
    messages = tuple(m for e in timeline if (m := _message(e, names)) is not None)
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


def _bridge(state: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    """The room's `m.bridge` content when it is a Messenger portal, or None."""
    bridge = next((c for (kind, _), c in state.items() if kind == BRIDGE), None)
    # RULE: only the bridges messenger rooms are read
    if not bridge or (bridge.get("protocol") or {}).get("id") != PROTOCOL:
        return None
    return bridge


def _message(event: dict[str, Any], names: dict[str, str]) -> Message | None:
    """One message event as a `Message`; None for anything that is not one."""
    kind = event.get("type")
    if kind not in {"m.room.message", "m.room.encrypted"} or "state_key" in event:
        return None
    sender = str(event.get("sender", ""))
    who = names.get(sender) or sender.removeprefix("@").split(":")[0] or "someone"
    at = dt.datetime.fromtimestamp(int(event.get("origin_server_ts", 0)) / 1000, tz=dt.UTC)
    if kind == "m.room.encrypted":
        return Message(who, at, kind="encrypted message")
    content = event.get("content") or {}
    sort = KINDS.get(str(content.get("msgtype")), "other")
    body = str(content.get("body", "")) if sort == "text" else ""
    return Message(who, at, body, sort)
