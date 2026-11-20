"""sms-bridge's Matrix side: one room per sender on the laptop's own Synapse, as an appservice.

The bridge is an appservice with a registration of its own (`registration`), which Synapse
reads. It owns the users `@sms_...` on the owner's server, one ghost per sender, and its bot
`@smsbot`. For each sender it makes one direct room, invites the owner and the ghost, and marks
the room with `m.bridge` state from its bot, protocol `sms`, as a one-to-one chat, the way
mautrix bridges mark theirs. Each text is sent as its ghost, at the time the phone received it,
with a transaction ID made from the phone's id, so a text sent again is taken once.

Notices from the bridge itself go to one room of their own, never marked as a chat.

A room is made once. Its ID is kept the moment Synapse gives it, before the ghost joins, and one
lock holds while a room is found or made, so a stop half way, a failed join, or two first texts
at once never give a sender a second room.

No room is encrypted, as for mautrix-meta in v1 (the owner's choice, deferred row D-enc). Only a
Synapse on this laptop is ever given the bridge's token. The bridge's own log holds no number,
name or word of a text: the room list holds them, readable by the owner only. A number is part of
its ghost's Matrix ID, as with mautrix bridges, so Synapse's own logs on this laptop hold it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
import urllib.parse
from typing import TYPE_CHECKING, Any

import yaml

from messages_mcp.bridge_config import write_private
from messages_mcp.device import NotSetUpError
from messages_mcp.matrix import on_this_laptop
from messages_mcp.sms_bridge import Sms

if TYPE_CHECKING:
    import pathlib

__all__ = ["FLAGS", "MatrixRooms", "RoomsError", "Sms", "as_token", "ghost_of", "registration", "write_registration"]

log = logging.getLogger(__name__)

PROTOCOL = "sms"
BOT_USERNAME = "smsbot"
GHOST = "sms_"
#: Where a text carries the phone's flags: held back as a one-time code, or cut at 2000.
FLAGS = "net.messages_mcp.sms"
#: Where mautrix bridges mark a one-to-one room; messages-mcp's reader trusts only that.
ROOM_TYPE = "com.beeper.room_type"
E164 = re.compile(r"\+[1-9][0-9]{1,14}")
SHORT_CODE = re.compile(r"[0-9]{1,6}")
CLIENT = "/_matrix/client/v3"
#: The key in the room list for the bridge's own notice room.
NOTICES = "notices"
#: The key in the room list that says a ghost has joined its room.
JOINED = "joined "
#: Seconds for one request to the laptop's own Synapse.
TIMEOUT = 30


class RoomsError(Exception):
    """Synapse refused or failed a request; the message names the request, never a sender."""


def registration(server: str) -> dict[str, Any]:
    """A new registration for the bridge on `server`, with tokens of its own."""
    return {
        "id": "sms-bridge",
        # It only sends: Synapse has nothing to push to it.
        "url": None,
        "as_token": secrets.token_urlsafe(32),
        "hs_token": secrets.token_urlsafe(32),
        "sender_localpart": BOT_USERNAME,
        "namespaces": {
            "users": [{"exclusive": True, "regex": f"@{GHOST}.*:{re.escape(server)}"}],
            "aliases": [],
            "rooms": [],
        },
        "rate_limited": False,
        "receive_ephemeral": False,
    }


def write_registration(path: pathlib.Path, server: str) -> bool:
    """Write a registration to `path`, 0600, unless there is one; whether it was written."""
    # RULE: the registration is never overwritten
    if path.exists():
        return False
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    write_private(path, yaml.safe_dump(registration(server), sort_keys=False))
    return True


def as_token(path: pathlib.Path) -> str:
    """The bridge's own token, from its registration at `path`."""
    try:
        token = yaml.safe_load(path.read_text(encoding="utf-8"))["as_token"]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as missing:
        msg = f"no usable registration in {path}; make one as docs/setup.md says"
        raise NotSetUpError(msg) from missing
    if not isinstance(token, str) or not token:
        msg = f"the registration in {path} has no token; make a new one as docs/setup.md says"
        raise NotSetUpError(msg)
    return token


def ghost_of(sender: str, kind: str, server: str) -> str:
    """The Matrix ID of the ghost who speaks for `sender` in its room."""
    if kind == "number" and E164.fullmatch(sender):
        return f"@{GHOST}{sender[1:]}:{server}"
    if kind == "short_code" and SHORT_CODE.fullmatch(sender):
        return f"@{GHOST}short_{sender}:{server}"
    # RULE: a name is never part of a matrix id
    hashed = hashlib.sha256(sender.casefold().encode()).hexdigest()[:20]
    return f"@{GHOST}name_{hashed}:{server}"


def _transaction(ident: str) -> str:
    """The same transaction ID for the same text each time it is sent."""
    # RULE: a retried text reuses its transaction id
    return "sms-" + hashlib.sha256(ident.encode()).hexdigest()[:32]


class MatrixRooms:
    """The rooms on `homeserver`, reached with the bridge's `token`, kept in `rooms_file`."""

    def __init__(self, homeserver: str, token: str, owner: str, rooms_file: pathlib.Path) -> None:
        """Post as the bridge for `owner`, whose server is the bridge's too."""
        self.homeserver = homeserver.rstrip("/")
        self.owner = owner
        self.server = owner.split(":", 1)[1]
        self.bot = f"@{BOT_USERNAME}:{self.server}"
        self.rooms_file = rooms_file
        self._token = token
        self._making = asyncio.Lock()

    def check(self) -> None:
        """`NotSetUpError`, naming the file, when the room list is there but cannot be read as one."""
        try:
            self._known()
        except RoomsError as broken:
            raise NotSetUpError(str(broken)) from broken

    async def post(self, sms: Sms) -> None:
        """Send `sms` as its sender's ghost into the sender's room, making both the first time."""
        ghost = ghost_of(sms.sender, sms.sender_kind, self.server)
        room = await self._room_of(ghost, sms.sender)
        stamp = str(int(sms.received_at.timestamp() * 1000))
        content = {"msgtype": "m.text", "body": sms.text, FLAGS: {"masked": sms.masked, "cut": sms.cut}}
        # RULE: a text is sent as its sender at the time the phone received it
        query = {"user_id": ghost, "ts": stamp}
        await self._ask("PUT", f"/rooms/{_q(room)}/send/m.room.message/{_transaction(sms.ident)}", content, query)

    async def notice(self, words: str) -> None:
        """Send `words` from the bridge's bot into its notice room, making that the first time."""
        async with self._making:
            known = self._known()
            room = known.get(NOTICES)
            if room is None:
                asked = {"preset": "private_chat", "name": "SMS bridge", "invite": [self.owner]}
                made = await self._ask("POST", "/createRoom", asked)
                room = str(made["room_id"])
                self._keep({**known, NOTICES: room})
        # Random, not derived: the same words in a later outage must be posted again. A notice is
        # sent again only when its post failed.
        txn = "notice-" + secrets.token_hex(16)
        await self._ask("PUT", f"/rooms/{_q(room)}/send/m.room.message/{txn}", {"msgtype": "m.notice", "body": words})

    async def _room_of(self, ghost: str, shown: str) -> str:
        """The room for `ghost`, made, with the ghost registered and joined, the first time."""
        # RULE: two first texts at once from a sender make one room
        async with self._making:
            known = self._known()
            room = known.get(ghost)
            # RULE: a sender keeps one room
            if room is None:
                room = await self._make(ghost, shown)
                # RULE: a room is kept as soon as it is made
                self._keep({**self._known(), ghost: room})
                log.info("made a room for a new sender")
            if JOINED + ghost not in known:
                await self._ask("POST", f"/join/{_q(room)}", {}, {"user_id": ghost})
                self._keep({**self._known(), JOINED + ghost: room})
            return room

    async def _make(self, ghost: str, shown: str) -> str:
        """Register `ghost` and make its direct room, marked as an SMS chat; the room's ID."""
        await self._register(ghost, shown)
        bridge = {
            "bridgebot": self.bot,
            "protocol": {"id": PROTOCOL, "displayname": "SMS"},
            "channel": {"id": ghost, "displayname": shown},
            ROOM_TYPE: "dm",
        }
        made = await self._ask(
            "POST",
            "/createRoom",
            {
                "preset": "private_chat",
                "name": shown,
                "is_direct": True,
                "invite": [self.owner, ghost],
                "initial_state": [{"type": "m.bridge", "state_key": f"{PROTOCOL}/{ghost}", "content": bridge}],
            },
        )
        return str(made["room_id"])

    async def _register(self, ghost: str, shown: str) -> None:
        """Register `ghost` and give it the name the phone gave its sender; one already there is fine."""
        local = ghost.removeprefix("@").split(":", 1)[0]
        body = {"type": "m.login.application_service", "username": local, "inhibit_login": True}
        await self._ask("POST", "/register", body, taken_is_fine=True)
        await self._ask("PUT", f"/profile/{_q(ghost)}/displayname", {"displayname": shown}, {"user_id": ghost})

    def _known(self) -> dict[str, str]:
        try:
            kept = json.loads(self.rooms_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as broken:
            msg = f"the room list in {self.rooms_file} could not be read"
            raise RoomsError(msg) from broken
        if not isinstance(kept, dict):
            msg = f"the room list in {self.rooms_file} is not a list of rooms"
            raise RoomsError(msg)
        return {str(k): str(v) for k, v in kept.items()}

    def _keep(self, known: dict[str, str]) -> None:
        self.rooms_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_private(self.rooms_file, json.dumps(known, indent=1, sort_keys=True) + "\n")

    async def _ask(
        self,
        method: str,
        path: str,
        body: dict[str, Any],
        query: dict[str, str] | None = None,
        *,
        taken_is_fine: bool = False,
    ) -> dict[str, Any]:
        """One request to Synapse as the bridge; `RoomsError` when it is not taken."""
        # RULE: only a synapse on this laptop gets the bridges token
        if not on_this_laptop(self.homeserver):
            msg = "sms-bridge's homeserver is not on this laptop, so it is never given the bridge's token"
            raise NotSetUpError(msg)
        # aiohttp is a slow import, and only a real post needs it.
        from aiohttp import ClientError, ClientSession, ClientTimeout  # noqa: PLC0415

        url = self.homeserver + CLIENT + path
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with (
                ClientSession(timeout=ClientTimeout(total=TIMEOUT)) as session,
                session.request(method, url, json=body, params=query or {}, headers=headers) as answer,
            ):
                given = await answer.json(content_type=None)
                status = answer.status
        except (ClientError, TimeoutError, ValueError) as failed:
            msg = f"{_what(path)} did not get an answer from Synapse ({type(failed).__name__})"
            raise RoomsError(msg) from failed
        if status == 200 and isinstance(given, dict):  # noqa: PLR2004 - HTTP's own number
            return given
        code = given.get("errcode") if isinstance(given, dict) else None
        if taken_is_fine and code == "M_USER_IN_USE":
            return {}
        msg = f"{_what(path)} was refused by Synapse: {status} {code or ''}".rstrip()
        raise RoomsError(msg)


def _q(part: str) -> str:
    """`part` made safe inside a URL path."""
    return urllib.parse.quote(part, safe="")


def _what(path: str) -> str:
    """The kind of request at `path`, never the room, user or sender in it."""
    first = path.strip("/").split("/", 1)[0]
    return {"rooms": "a send", "createRoom": "making a room", "join": "a join", "register": "registering a sender"}.get(
        first, "setting a sender's name"
    )
