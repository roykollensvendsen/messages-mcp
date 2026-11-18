"""matrix-door: the only way into the owner's Matrix inbox and its pushes from the tailnet.

It listens on this laptop's own tailnet addresses: port 8448 for Synapse and 2586 for ntfy.
For each new connection it first asks tailscaled which device is calling. That happens before
it reads a single byte of the connection, and before any TLS. A device gets in only when it is
on the owner's list (`door_list`), and is neither tagged nor shared in from another tailnet.
Anything else, a slow or failed answer from tailscaled included, is closed at once and written
as one line to the refusal log. A device that gets in is passed, over TLS, to the service on
127.0.0.1.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime as dt
import os
import re
import socket
import ssl
from typing import TYPE_CHECKING, Any

from messages_mcp.door_list import DoorListError
from messages_mcp.localapi import LocalAPIError

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Awaitable, Callable

#: How long tailscaled may take to say who is calling before the call is refused.
WHOIS_TIMEOUT = 2.0
#: How long a device that got in may take over its TLS handshake.
HANDSHAKE_TIMEOUT = 10.0
#: How often a running door asks again whether Funnel or Tailscale serve opened a way round it.
CHECK_EVERY = 60.0
#: How long the refusal log keeps a line.
KEEP_DAYS = 30
#: The most lines the refusal log keeps, newest first, whatever their age.
KEEP_LINES = 10_000
#: One line per caller in this many seconds; the refusals in between are counted on the next.
QUIET = 60.0
#: How long a call that got in may go with no byte either way before the door ends it.
IDLE_TIMEOUT = 300.0
#: Where the services behind the door listen, on this laptop only.
UPSTREAM_HOST = "127.0.0.1"
CHUNK = 1 << 16


@dataclasses.dataclass(frozen=True)
class Route:
    """One port the door listens on, and the local port it passes a device that got in to."""

    listen: int
    upstream: int


#: Synapse's federation port, and ntfy's port.
ROUTES = (Route(8448, 8008), Route(2586, 2586))


@dataclasses.dataclass(frozen=True)
class Verdict:
    """Whether a caller gets in, and who it was and why, for the refusal log."""

    allowed: bool
    reason: str
    name: str = ""
    stable_id: str = ""


class DoorError(Exception):
    """The door will not start; the message names why."""


async def check(
    ip: str,
    port: int,
    ask: Callable[[str, int], Awaitable[dict[str, Any]]],
    allow: Callable[[], frozenset[str]],
) -> Verdict:
    """Whether the device calling from `ip`:`port` gets in; every doubt is a refusal."""
    try:
        node = await ask(ip, port)
    except LocalAPIError as failed:
        # RULE: a slow answer from tailscaled is a refusal
        slow = isinstance(failed.__cause__, TimeoutError)
        return Verdict(allowed=False, reason="tailscaled was too slow to say who it is" if slow else "whois failed")
    stable_id = str(node.get("StableID", ""))
    name = str(node.get("ComputedName") or str(node.get("Name", "")).split(".")[0])
    # RULE: a tagged device is refused even when it is listed
    if node.get("Tags"):
        return Verdict(allowed=False, reason="tagged", name=name, stable_id=stable_id)
    # RULE: a device shared in from another tailnet is refused even when it is listed
    if node.get("Sharer"):
        return Verdict(allowed=False, reason="shared in from another tailnet", name=name, stable_id=stable_id)
    try:
        listed = allow()
    except DoorListError:
        return Verdict(allowed=False, reason="the device list cannot be read", name=name, stable_id=stable_id)
    # RULE: a device not on the list is refused
    if stable_id not in listed:
        return Verdict(allowed=False, reason="not on the list", name=name, stable_id=stable_id)
    return Verdict(allowed=True, reason="listed", name=name, stable_id=stable_id)


class RefusalLog:
    """The refusals, kept on this laptop for `KEEP_DAYS` days and readable by the owner only.

    A caller that keeps knocking gets one line in each `QUIET` seconds. The line after that
    says how many were refused in between, so a loop never floods the log or the disk.
    """

    def __init__(self, path: pathlib.Path, now: Callable[[], dt.datetime] | None = None) -> None:
        """Write to `path`, telling the time with `now`."""
        self.path = path
        self.now = now or (lambda: dt.datetime.now(dt.UTC))
        self._last: dict[str, tuple[dt.datetime, int]] = {}

    def write(self, verdict: Verdict) -> None:
        """Add the refusal, or count it when its caller already has a line this minute."""
        now = self.now()
        caller = verdict.stable_id or verdict.reason
        last, between = self._last.get(caller, (None, 0))
        # RULE: a caller that keeps knocking gets one line a minute
        if last is not None and (now - last).total_seconds() < QUIET:
            self._last[caller] = (last, between + 1)
            return
        self._last[caller] = (now, 0)
        reason = f"{verdict.reason} ({between} more before this)" if between else verdict.reason
        fields = [now.isoformat(timespec="seconds"), verdict.name or "-", verdict.stable_id or "-", reason]
        # RULE: a refusal is one line however its device is named
        line = "\t".join(re.sub(r"\s+", " ", field).strip() or "-" for field in fields) + "\n"
        kept = [old for old in self._lines() if self._young(old, now)][-(KEEP_LINES - 1) :]
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.writelines([*kept, line])

    def _lines(self) -> list[str]:
        try:
            return self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        except FileNotFoundError:
            return []

    @staticmethod
    def _young(line: str, now: dt.datetime) -> bool:
        try:
            when = dt.datetime.fromisoformat(line.split("\t", 1)[0])
        except ValueError:
            return False
        # RULE: the refusal log keeps a line for thirty days
        return now - when <= dt.timedelta(days=KEEP_DAYS)


class Door:
    """Accepts connections, checks each caller, and passes the ones that get in to their service."""

    def __init__(
        self,
        ask: Callable[[str, int], Awaitable[dict[str, Any]]],
        allow: Callable[[], frozenset[str]],
        log: RefusalLog,
        tls: ssl.SSLContext | None,
        idle: float = IDLE_TIMEOUT,
    ) -> None:
        """Check callers with `ask` and `allow`, and pass the ones that get in over `tls`."""
        self.ask, self.allow, self.log, self.tls = ask, allow, log, tls
        self.idle = idle
        self._tasks: set[asyncio.Task[None]] = set()

    async def handle(self, sock: socket.socket, upstream: int) -> None:
        """Check the caller on `sock`, then pass it to `upstream` or close it."""
        ip, port = sock.getpeername()[:2]
        verdict = await check(ip, port, self.ask, self.allow)
        # RULE: a refused connection is closed before any byte of it is read
        if not verdict.allowed:
            sock.close()
            self.log.write(verdict)
            return
        await self._pass(sock, upstream)

    async def _pass(self, sock: socket.socket, upstream: int) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        timeout = HANDSHAKE_TIMEOUT if self.tls else None
        try:
            transport, _ = await loop.connect_accepted_socket(
                lambda: protocol, sock, ssl=self.tls, ssl_handshake_timeout=timeout
            )
        except (OSError, ssl.SSLError, TimeoutError):
            sock.close()
            return
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        try:
            up_reader, up_writer = await asyncio.open_connection(UPSTREAM_HOST, upstream)
        except OSError:
            writer.close()
            return
        heard = [loop.time()]
        both = asyncio.gather(_pump(reader, up_writer, heard), _pump(up_reader, writer, heard))
        # RULE: a call with no byte either way for the idle time is ended
        while not both.done():
            await asyncio.wait([both], timeout=self.idle)
            if not both.done() and loop.time() - heard[0] >= self.idle:
                for end in (writer, up_writer):
                    end.close()
                await both

    def listen(self, host: str, port: int) -> socket.socket:
        """A listening socket on `host`:`port`; port 0 takes any free one."""
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        listener = socket.socket(family, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen()
        listener.setblocking(False)  # noqa: FBT003 - the socket API takes it positionally
        return listener

    async def serve(self, listeners: list[tuple[socket.socket, int]]) -> None:
        """Accept on every listener until cancelled, each passing to its own upstream port."""
        try:
            await asyncio.gather(*(self._accept(listener, upstream) for listener, upstream in listeners))
        finally:
            for listener, _ in listeners:
                listener.close()

    async def _accept(self, listener: socket.socket, upstream: int) -> None:
        loop = asyncio.get_running_loop()
        while True:
            sock, _ = await loop.sock_accept(listener)
            task = asyncio.create_task(self.handle(sock, upstream))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)


async def _pump(source: asyncio.StreamReader, sink: asyncio.StreamWriter, heard: list[float]) -> None:
    """Copy `source` to `sink` until either end closes, noting in `heard` when bytes last went by."""
    loop = asyncio.get_running_loop()
    try:
        while chunk := await source.read(CHUNK):
            heard[0] = loop.time()
            sink.write(chunk)
            await sink.drain()
    except (ConnectionError, ssl.SSLError):
        pass
    finally:
        with contextlib.suppress(ConnectionError, ssl.SSLError, RuntimeError):
            sink.close()


def tls_context(cert: pathlib.Path, key: pathlib.Path) -> ssl.SSLContext:
    """The server side of TLS, with the laptop's `tailscale cert` certificate."""
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context


async def start(
    api: Any,  # noqa: ANN401 - a LocalAPI or a test's stand-in
    allow: Callable[[], frozenset[str]],
    log: RefusalLog,
    tls: ssl.SSLContext | None,
    every: float = CHECK_EVERY,
) -> None:
    """Listen on every own address while no way round the door is open, asking again `every` seconds."""
    await _no_way_round(api)
    try:
        hosts = await api.own_addresses()
    except LocalAPIError as unsure:
        msg = f"matrix-door cannot start: {unsure}"
        raise DoorError(msg) from unsure
    door = Door(api.whois, allow, log, tls)
    listeners = [(door.listen(host, route.listen), route.upstream) for host in hosts for route in ROUTES]
    try:
        async with asyncio.TaskGroup() as running:
            running.create_task(door.serve(listeners))
            running.create_task(_watch(api, every))
    except* DoorError as stopped:
        raise stopped.exceptions[0] from None


async def _watch(api: Any, every: float) -> None:  # noqa: ANN401
    """Stop the door as soon as a way round it opens."""
    while True:
        await asyncio.sleep(every)
        # RULE: a running door stops when a way round it opens
        await _no_way_round(api)


async def _no_way_round(api: Any) -> None:  # noqa: ANN401
    """Raise DoorError when Funnel opens a door port, Tailscale serve passes on a service, or it can't tell."""
    try:
        funnelled, passed = await api.exposure()
    except LocalAPIError as unsure:
        msg = f"matrix-door cannot run, since it cannot tell whether a way round it is open: {unsure}"
        raise DoorError(msg) from unsure
    ports = {route.listen for route in ROUTES}
    # RULE: the door will not start while Funnel opens one of its ports
    if funnelled & ports:
        msg = f"Funnel opens port {', '.join(map(str, sorted(funnelled & ports)))} to the internet; turn it off first"
        raise DoorError(msg)
    services = {route.upstream for route in ROUTES}
    # RULE: the door will not start while Tailscale serve passes on a service behind it
    if passed & services:
        msg = f"Tailscale serve passes port {', '.join(map(str, sorted(passed & services)))} past the door; remove it"
        raise DoorError(msg)
