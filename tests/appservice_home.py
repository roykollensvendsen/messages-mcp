"""A made-up Synapse for sms-bridge's Matrix side: the appservice's part of the client API, recorded.

It answers registering a ghost, setting its name, making a room, joining one and sending into
one, and refuses everything else with 404 after writing it down. Only the bridge's own token is
let in. A send with a transaction ID already used by that user is taken once, as Synapse does.
Every server, user and word in it is made up.
"""

import asyncio
import contextlib
import json
import urllib.parse

from aiohttp import web

AS_TOKEN = "made-up-as-token"  # noqa: S105 - made up
SERVER = "made-up-laptop.example.invalid"
OWNER = f"@owner:{SERVER}"
BOT = f"@smsbot:{SERVER}"
#: A bound on each wait for the made-up server, so a test that would hang fails instead.
WITHIN = 30
PREFIX = "/_matrix/client/v3"


class AppserviceHome:
    """The made-up Synapse: `rooms` maps a room ID to what was made and sent there."""

    def __init__(self):
        self.requests = []
        self.users = set()
        self.names = {}
        self.rooms = {}
        self.sent = {}
        #: Paths that fail once each, with a 500, the next time they are asked.
        self.fail_next = []
        self.url = ""
        self._runner = None

    async def __aenter__(self):
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._answer)
        self._runner = web.AppRunner(app, access_log=None)
        await asyncio.wait_for(self._runner.setup(), WITHIN)
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await asyncio.wait_for(site.start(), WITHIN)
        self.url = f"http://127.0.0.1:{self._runner.addresses[0][1]}"
        return self

    async def __aexit__(self, *_):
        await asyncio.wait_for(self._runner.cleanup(), WITHIN)

    def asked(self, method, part):
        """The requests of `method` whose path holds `part`."""
        return [r for r in self.requests if r["method"] == method and part in r["path"]]

    def messages(self, room):
        """The events sent into `room`, in order."""
        return [event for (where, _, _), event in self.sent.items() if where == room]

    async def _answer(self, request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ")
        body = await request.read()
        path = urllib.parse.unquote(request.path)
        asked = {"method": request.method, "path": path, "query": dict(request.query), "token": token}
        asked["body"] = json.loads(body) if body else None
        self.requests.append(asked)
        if token != AS_TOKEN:
            return web.json_response({"errcode": "M_UNKNOWN_TOKEN"}, status=401)
        for failing in list(self.fail_next):
            if failing in path:
                self.fail_next.remove(failing)
                return web.json_response({"errcode": "M_UNKNOWN", "error": "made-up failure"}, status=500)
        as_user = request.query.get("user_id", BOT)
        return self._route(request.method, path.removeprefix(PREFIX), asked["body"] or {}, as_user, request.query)

    def _route(self, method, path, body, as_user, query):
        if method == "POST" and path == "/register":
            return self._register(body)
        if method == "PUT" and path.startswith("/profile/") and path.endswith("/displayname"):
            self.names[path.split("/")[2]] = body.get("displayname")
            return web.json_response({})
        if method == "POST" and path == "/createRoom":
            return self._create(body, as_user)
        if method == "POST" and path.startswith("/join/"):
            room = path.removeprefix("/join/")
            self.rooms[room]["joined"].append(as_user)
            return web.json_response({"room_id": room})
        if method == "PUT" and path.startswith("/rooms/") and "/send/" in path:
            return self._send(path, body, as_user, query)
        return web.json_response({"errcode": "M_UNRECOGNIZED"}, status=404)

    def _register(self, body):
        user = f"@{body.get('username')}:{SERVER}"
        if body.get("type") != "m.login.application_service":
            return web.json_response({"errcode": "M_FORBIDDEN"}, status=403)
        if user in self.users:
            return web.json_response({"errcode": "M_USER_IN_USE", "error": "taken"}, status=400)
        self.users.add(user)
        return web.json_response({"user_id": user})

    def _create(self, body, as_user):
        room = f"!made-up-room-{len(self.rooms) + 1}:{SERVER}"
        self.rooms[room] = {"creator": as_user, "made": body, "joined": [as_user]}
        return web.json_response({"room_id": room})

    def _send(self, path, body, as_user, query):
        _, _, room, _, kind, txn = path.split("/", 6)
        key = (room, as_user, txn)
        if key not in self.sent:
            self.sent[key] = {"type": kind, "sender": as_user, "content": body, "ts": query.get("ts")}
        return web.json_response({"event_id": f"$made-up-{txn}"})


@contextlib.asynccontextmanager
async def serving():
    """The made-up Synapse, running for the length of the block."""
    async with AppserviceHome() as server:
        yield server
