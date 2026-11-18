"""A made-up Synapse for the Matrix reader's tests: just enough of the client API, all of it recorded.

It answers `/login`, `/sync` and Element's private receipt, and refuses everything else with
404 after writing it down, so a test can say exactly what the plugin asked. Every room,
person and word in it is made up. Rooms are built with `portal()` and `plain()`.
"""

import asyncio
import contextlib
import json

from aiohttp import web

PLUGIN_TOKEN = "made-up-plugin-token"  # noqa: S105 - made up
ELEMENT_TOKEN = "made-up-element-token"  # noqa: S105 - made up
OWNER = "@owner:made-up-laptop.example.invalid"
PASSWORD = "made-up-password"  # noqa: S105 - made up
#: A bound on each wait for the made-up server, so a test that would hang fails instead.
WITHIN = 30


def _event(kind, content, *, sender="@bridgebot:made-up", key=None, ts=0, event_id=None):  # noqa: PLR0913 - an event's own fields
    event = {"type": kind, "content": content, "sender": sender, "origin_server_ts": ts}
    if key is not None:
        event["state_key"] = key
    if event_id:
        event["event_id"] = event_id
    return event


def text(sender, body, ts, msgtype="m.text"):
    """A message event, as the bridge's ghost user sends it."""
    return {"sender": sender, "body": body, "ts": ts, "msgtype": msgtype}


def portal(name, receiver, messages, *, members=None, protocol="facebook", encrypted=()):  # noqa: PLR0913 - a room's own parts
    """A bridged Messenger room, with its `m.bridge` state and `messages` as text events."""
    bridge = {
        "bridgebot": "@bridgebot:made-up",
        "protocol": {"id": protocol, "displayname": "Made-up Network"},
        "channel": {"id": "made-up-thread", "displayname": name, "fi.mau.receiver": receiver},
    }
    if not receiver:
        del bridge["channel"]["fi.mau.receiver"]
    state = [_event("m.bridge", bridge, key="made-up-bridge-id"), _event("m.room.name", {"name": name}, key="")]
    state += [
        _event("m.room.member", {"displayname": shown, "membership": "join"}, key=who)
        for who, shown in (members or {}).items()
    ]
    timeline = [
        _event(
            "m.room.message",
            {"msgtype": m["msgtype"], "body": m["body"]},
            sender=m["sender"],
            ts=m["ts"],
            event_id=f"$made-up-{i}",
        )
        for i, m in enumerate(messages)
    ]
    timeline += [
        _event("m.room.encrypted", {"algorithm": "m.megolm.v1.aes-sha2"}, sender=who, ts=ts) for who, ts in encrypted
    ]
    return {"state": state, "timeline": timeline}


def plain(name, messages):
    """A Matrix room that is not a portal, such as the bridge's management room."""
    room = portal(name, "x", messages)
    room["state"] = [e for e in room["state"] if e["type"] != "m.bridge"]
    return room


class Homeserver:
    """The made-up Synapse. `rooms` maps a room ID to a room; `unread` to its count."""

    def __init__(self, rooms=None, unread=None):
        self.rooms = rooms or {}
        self.unread = dict(unread or {})
        self.requests = []
        self.url = ""
        self._runner = None

    async def __aenter__(self):
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._answer)
        self._runner = web.AppRunner(app, access_log=None)
        await asyncio.wait_for(self._runner.setup(), WITHIN)
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await asyncio.wait_for(site.start(), WITHIN)
        port = self._runner.addresses[0][1]
        self.url = f"http://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *_):
        await asyncio.wait_for(self._runner.cleanup(), WITHIN)

    def asked_by(self, token):
        """Each request made with `token`, as (method, path, query)."""
        return [(m, p, q) for m, p, q, t in self.requests if t == token]

    async def _answer(self, request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        body = await request.read()
        self.requests.append((request.method, request.path, dict(request.query), token))
        path = request.path
        if request.method == "POST" and path.endswith("/login"):
            return self._login(json.loads(body or b"{}"))
        if token not in {PLUGIN_TOKEN, ELEMENT_TOKEN}:
            return web.json_response({"errcode": "M_UNKNOWN_TOKEN"}, status=401)
        if request.method == "GET" and path.endswith("/sync"):
            return web.json_response(self._sync())
        if request.method == "POST" and "/receipt/m.read.private/" in path:
            room = path.split("/rooms/")[1].split("/")[0]
            self.unread[room] = 0
            return web.json_response({})
        return web.json_response({"errcode": "M_UNRECOGNIZED"}, status=404)

    def _login(self, asked):
        user = (asked.get("identifier") or {}).get("user") or asked.get("user")
        if user != OWNER or asked.get("password") != PASSWORD:
            return web.json_response({"errcode": "M_FORBIDDEN", "error": "Invalid username or password"}, status=403)
        return web.json_response({"user_id": OWNER, "device_id": "MADEUPDEVICE", "access_token": PLUGIN_TOKEN})

    def _sync(self):
        join = {}
        for room_id, room in self.rooms.items():
            join[room_id] = {
                "state": {"events": room["state"]},
                "timeline": {"events": room["timeline"], "limited": False},
                "unread_notifications": {"notification_count": self.unread.get(room_id, 0)},
            }
        return {"next_batch": "made-up-batch", "rooms": {"join": join}}


@contextlib.asynccontextmanager
async def serving(rooms=None, unread=None):
    """The made-up Synapse, running for the length of the block."""
    async with Homeserver(rooms, unread) as server:
        yield server
