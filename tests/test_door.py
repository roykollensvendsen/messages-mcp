"""matrix-door lets in only the owner's listed devices, and closes everything else unread.

Every test here talks to the real door over real sockets. tailscaled is a stand-in on a Unix
socket of the test's own, answering as the real one does, or never. Behind the door sits a
stand-in service that records what reached it. The devices, their names and StableIDs are made up.
"""

import asyncio
import datetime as dt
import json
import socket
import ssl
import struct
import urllib.parse

import pytest
from bounded import run_bounded

from messages_mcp import door, door_list
from messages_mcp.localapi import LocalAPI, LocalAPIError

PHONE = {"StableID": "nFAKE0PHONE0CNTRL", "ComputedName": "made-up-phone", "Name": "made-up-phone.example."}
LIST = '[[device]]\nname = "phone"\nid = "nFAKE0PHONE0CNTRL"\n'
REQUEST = b"GET /_matrix/client/versions HTTP/1.0\r\n\r\n"
#: Every wait in a test is bounded, so a door that hangs fails the test instead.
WITHIN = 30


class FakeTailscaled:
    """Answers the local API's paths from `answers`; a path answered with None never gets a reply."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    async def handle(self, reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        path = request.split(b" ", 2)[1].decode()
        self.asked.append(path)
        answer = self.answers.get(path.split("?", 1)[0], (404, {}))
        if answer is None:
            await asyncio.sleep(WITHIN)
            return
        status, body = answer
        writer.write(
            f"HTTP/1.0 {status} X\r\nContent-Type: application/json\r\n\r\n".encode() + json.dumps(body).encode()
        )
        await writer.drain()
        writer.close()


class Upstream:
    """A service behind the door that records what reached it and answers each chunk."""

    def __init__(self, word):
        self.word = word
        self.got = b""

    async def handle(self, reader, writer):
        while chunk := await reader.read(1024):
            self.got += chunk
            writer.write(self.word + b": " + chunk)
            await writer.drain()
        writer.close()


@pytest.fixture
def place(tmp_path, monkeypatch):
    """The test's folder as the working one: a Unix socket's path is short there."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _list(place, text=LIST, mode=0o600):
    path = place / "door.toml"
    path.write_text(text)
    path.chmod(mode)
    return lambda: door_list.allowed(path)


async def _scenario(place, answers, allow, *, ask=None, host="127.0.0.1", send=REQUEST):  # noqa: PLR0913
    """One caller, through the door: what the caller got back, what the service got, and the log.

    `ask`, when given, stands in for tailscaled and is also handed the door's side of the socket.
    """
    api_server = FakeTailscaled(answers)
    unix = await asyncio.start_unix_server(api_server.handle, "api.sock")
    upstream = Upstream(b"synapse")
    up = await asyncio.start_server(upstream.handle, "127.0.0.1", 0)
    log = door.RefusalLog(place / "state/door.log")
    api = LocalAPI("api.sock", timeout=0.3)
    accepted = None

    async def asking(ip, port):
        return await ask(accepted, ip, port)

    gate = door.Door(asking if ask else api.whois, allow, log, tls=None)
    listener = gate.listen(host, 0)
    loop = asyncio.get_running_loop()
    try:
        async with asyncio.timeout(WITHIN):
            reader, writer = await asyncio.open_connection(*listener.getsockname()[:2])
            writer.write(send)
            await writer.drain()
            accepted, _ = await loop.sock_accept(listener)
            handled = asyncio.create_task(gate.handle(accepted, up.sockets[0].getsockname()[1]))
            try:
                back = await reader.read(1024)
            except ConnectionResetError:
                back = b""
            writer.close()
            await handled
    finally:
        listener.close()
        unix.close()
        up.close()
    lines = log.path.read_text().splitlines() if log.path.exists() else []
    return back, upstream.got, lines, api_server.asked


def _run(*args, **kwargs):
    return asyncio.run(_scenario(*args, **kwargs))


def test_a_listed_device_is_passed_to_the_service_behind_the_door(place):
    back, got, lines, asked = _run(place, {"/localapi/v0/whois": (200, {"Node": PHONE})}, _list(place))
    assert got.startswith(b"GET /_matrix/client/versions")
    assert back.startswith(b"synapse: GET")
    assert lines == []
    (only,) = asked
    assert urllib.parse.urlsplit(only).path == "/localapi/v0/whois"
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(only).query)["addr"][0].startswith("127.0.0.1:")


def test_a_device_not_on_the_list_is_refused(place):
    other = {**PHONE, "StableID": "nFAKE0OTHER0CNTRL", "ComputedName": "made-up-laptop"}
    back, got, lines, _ = _run(place, {"/localapi/v0/whois": (200, {"Node": other})}, _list(place))
    assert (back, got) == (b"", b"")
    assert len(lines) == 1
    assert lines[0].split("\t")[1:] == ["made-up-laptop", "nFAKE0OTHER0CNTRL", "not on the list"]


def test_a_tagged_device_is_refused_even_when_it_is_listed(place):
    tagged = {**PHONE, "Tags": ["tag:server"]}
    back, got, lines, _ = _run(place, {"/localapi/v0/whois": (200, {"Node": tagged})}, _list(place))
    assert (back, got) == (b"", b"")
    assert [line.split("\t")[3] for line in lines] == ["tagged"]


def test_a_device_shared_in_from_another_tailnet_is_refused_even_when_it_is_listed(place):
    shared = {**PHONE, "Sharer": 12345}
    back, got, lines, _ = _run(place, {"/localapi/v0/whois": (200, {"Node": shared})}, _list(place))
    assert (back, got) == (b"", b"")
    assert [line.split("\t")[3] for line in lines] == ["shared in from another tailnet"]


def test_a_slow_answer_from_tailscaled_is_a_refusal(place):
    back, got, lines, _ = _run(place, {"/localapi/v0/whois": None}, _list(place))
    assert (back, got) == (b"", b"")
    assert [line.split("\t")[1:] for line in lines] == [["-", "-", "tailscaled was too slow to say who it is"]]


def test_an_error_from_tailscaled_is_a_refusal(place):
    back, got, lines, _ = _run(place, {"/localapi/v0/whois": (500, {})}, _list(place))
    assert (back, got) == (b"", b"")
    assert [line.split("\t")[3] for line in lines] == ["whois failed"]


def test_no_tailscaled_at_all_is_a_refusal(place):
    async def gone(_accepted, _ip, _port):
        return await LocalAPI(place / "nobody.sock", timeout=1).whois("127.0.0.1", 1)

    back, got, lines, _ = _run(place, {}, _list(place), ask=gone)
    assert (back, got) == (b"", b"")
    assert [line.split("\t")[3] for line in lines] == ["whois failed"]


@pytest.mark.parametrize("text", [None, "", '[[device]]\nname = "phone"\nid = ""\n'])
def test_a_missing_or_empty_list_lets_nobody_in(place, text):
    path = place / "door.toml"
    if text is not None:
        path.write_text(text)
        path.chmod(0o600)
    back, got, lines, _ = _run(place, {"/localapi/v0/whois": (200, {"Node": PHONE})}, lambda: door_list.allowed(path))
    assert (back, got) == (b"", b"")
    assert [line.split("\t")[3] for line in lines] == ["not on the list"]


def test_a_list_that_cannot_be_read_lets_nobody_in(place):
    back, got, lines, _ = _run(place, {"/localapi/v0/whois": (200, {"Node": PHONE})}, _list(place, "[[device"))
    assert (back, got) == (b"", b"")
    assert [line.split("\t")[3] for line in lines] == ["the device list cannot be read"]


def test_a_refused_connection_is_closed_before_any_byte_of_it_is_read(place):
    """While the door asks who is calling, every byte the caller sent is still unread in the socket."""
    seen = {}

    async def peeking(accepted, _ip, _port):
        seen["socket"] = accepted
        try:
            seen["unread"] = accepted.recv(4096, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except BlockingIOError:
            seen["unread"] = b""  # the door took them already
        return {**PHONE, "StableID": "nFAKE0OTHER0CNTRL"}

    send = b"GET /made-up HTTP/1.0\r\n\r\n"
    back, got, lines, _ = _run(place, {}, _list(place), send=send, ask=peeking)
    assert seen["unread"] == send, "the door read the caller's bytes before it knew who was calling"
    assert (back, got) == (b"", b"")
    assert len(lines) == 1
    assert seen["socket"].fileno() == -1, "the refused connection is closed"


def test_a_refusal_is_one_line_however_its_device_is_named(place):
    sly = {**PHONE, "StableID": "nFAKE0SLY0CNTRL", "ComputedName": "made-up\nforged\tline"}
    _, _, lines, _ = _run(place, {"/localapi/v0/whois": (200, {"Node": sly})}, _list(place))
    assert len(lines) == 1
    assert lines[0].split("\t")[1:] == ["made-up forged line", "nFAKE0SLY0CNTRL", "not on the list"]


def test_the_refusal_log_is_readable_by_the_owner_only(place):
    _run(place, {"/localapi/v0/whois": (500, {})}, _list(place))
    assert (place / "state/door.log").stat().st_mode & 0o777 == 0o600
    assert (place / "state").stat().st_mode & 0o777 == 0o700


def test_the_refusal_log_keeps_a_line_for_thirty_days(tmp_path):
    now = dt.datetime(2026, 11, 18, 12, tzinfo=dt.UTC)
    path = tmp_path / "door.log"
    old = now - dt.timedelta(days=31)
    kept = now - dt.timedelta(days=29)
    path.write_text(f"{old.isoformat()}\t-\t-\twhois failed\n{kept.isoformat()}\t-\t-\twhois failed\n")
    door.RefusalLog(path, now=lambda: now).write(door.Verdict(allowed=False, reason="tagged"))
    assert [line.split("\t")[0] for line in path.read_text().splitlines()] == [
        kept.isoformat(),
        now.isoformat(timespec="seconds"),
    ]


def test_each_port_passes_to_its_own_service(place):
    """Ntfy's port goes through the same door, with the same list, to ntfy and not to Synapse."""

    async def scenario():
        api = FakeTailscaled({"/localapi/v0/whois": (200, {"Node": PHONE})})
        unix = await asyncio.start_unix_server(api.handle, "api.sock")
        synapse, ntfy = Upstream(b"synapse"), Upstream(b"ntfy")
        servers = [await asyncio.start_server(u.handle, "127.0.0.1", 0) for u in (synapse, ntfy)]
        gate = door.Door(LocalAPI("api.sock", 1).whois, _list(place), door.RefusalLog(place / "log"), tls=None)
        listeners = [(gate.listen("127.0.0.1", 0), s.sockets[0].getsockname()[1]) for s in servers]
        serving = asyncio.create_task(gate.serve(listeners))
        answers = []
        try:
            async with asyncio.timeout(WITHIN):
                for listener, _ in listeners:
                    reader, writer = await asyncio.open_connection(*listener.getsockname())
                    writer.write(b"hello")
                    answers.append(await reader.read(1024))
                    writer.close()
        finally:
            serving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await serving
            unix.close()
            for s in servers:
                s.close()
        return answers

    assert asyncio.run(scenario()) == [b"synapse: hello", b"ntfy: hello"]


def _certificate(place):
    cert, key = place / "door.crt", place / "door.key"
    run_bounded(
        [
            "openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=made-up-laptop",
            "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return cert, key


def test_a_listed_device_gets_in_over_tls(place):
    cert, key = _certificate(place)

    async def scenario():
        api = FakeTailscaled({"/localapi/v0/whois": (200, {"Node": PHONE})})
        unix = await asyncio.start_unix_server(api.handle, "api.sock")
        synapse = Upstream(b"synapse")
        up = await asyncio.start_server(synapse.handle, "127.0.0.1", 0)
        tls = door.tls_context(cert, key)
        gate = door.Door(LocalAPI("api.sock", 1).whois, _list(place), door.RefusalLog(place / "log"), tls)
        listener = gate.listen("127.0.0.1", 0)
        serving = asyncio.create_task(gate.serve([(listener, up.sockets[0].getsockname()[1])]))
        client = ssl.create_default_context(cafile=str(cert))
        try:
            async with asyncio.timeout(WITHIN):
                reader, writer = await asyncio.open_connection(*listener.getsockname(), ssl=client)
                writer.write(b"hello")
                answer = await reader.read(1024)
                writer.close()
        finally:
            serving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await serving
            unix.close()
            up.close()
        return answer, synapse.got

    assert asyncio.run(scenario()) == (b"synapse: hello", b"hello")


class StubAPI:
    """The local API's start-up and watch questions, answered from the test, one answer per ask.

    `exposures` is what each ask of serve-config says, as (Funnel's ports, the ports serve passes
    on); the last one repeats. `broken` makes every ask fail.
    """

    def __init__(self, exposures=((set(), set()),), hosts=("127.0.0.1",), *, broken=False):
        self.exposures, self.hosts, self.broken = list(exposures), list(hosts), broken
        self.asked = 0

    async def exposure(self):
        if self.broken:
            msg = "no answer"
            raise LocalAPIError(msg)
        self.asked += 1
        return self.exposures[min(self.asked, len(self.exposures)) - 1]

    async def own_addresses(self):
        return self.hosts

    async def whois(self, _ip, _port):
        return PHONE


def _no_real_ports(monkeypatch, listened):
    """Door.listen on a free port of 127.0.0.1, whatever port it is asked for, noting what was asked."""

    def listen(_self, host, port):
        listened.append((host, port))
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        sock.setblocking(False)  # noqa: FBT003 - the socket API takes it positionally
        return sock

    monkeypatch.setattr(door.Door, "listen", listen)


def _started(tmp_path, api, every=60.0):
    """Start the door against `api`, bounded: a door that started anyway would serve until stopped."""
    started = door.start(api, frozenset, door.RefusalLog(tmp_path / "log"), None, every=every)
    asyncio.run(asyncio.wait_for(started, 5))


def test_the_door_will_not_start_while_funnel_opens_one_of_its_ports(tmp_path, monkeypatch):
    _no_real_ports(monkeypatch, [])
    with pytest.raises(door.DoorError, match="Funnel opens port 8448"):
        _started(tmp_path, StubAPI([({8448, 443}, set())]))


def test_the_door_will_not_start_while_tailscale_serve_passes_on_a_service_behind_it(tmp_path, monkeypatch):
    """A serve proxy to Synapse or ntfy would let the whole tailnet round the door."""
    _no_real_ports(monkeypatch, [])
    with pytest.raises(door.DoorError, match="serve passes port 8008 past the door"):
        _started(tmp_path, StubAPI([(set(), {8008, 3000})]))


def test_a_running_door_stops_when_a_way_round_it_opens(tmp_path, monkeypatch):
    listened = []
    _no_real_ports(monkeypatch, listened)
    api = StubAPI([(set(), set()), (set(), set()), (set(), {2586})])
    with pytest.raises(door.DoorError, match="serve passes port 2586"):
        _started(tmp_path, api, every=0.05)
    assert len(listened) == 2, "it ran before it stopped"
    assert api.asked == 3


def test_the_door_will_not_start_when_it_cannot_tell_whether_a_way_round_it_is_open(tmp_path):
    with pytest.raises(door.DoorError, match="cannot tell"):
        _started(tmp_path, StubAPI(broken=True))


def test_the_door_will_not_start_with_no_tailnet_address(tmp_path):
    class NoAddress(StubAPI):
        async def own_addresses(self):
            msg = "no address"
            raise LocalAPIError(msg)

    with pytest.raises(door.DoorError, match="cannot start"):
        _started(tmp_path, NoAddress())


def test_with_no_way_round_it_the_door_listens_on_every_route(tmp_path, monkeypatch):
    listened = []
    _no_real_ports(monkeypatch, listened)

    async def scenario():
        api = StubAPI([({443}, {3000})])
        running = asyncio.create_task(door.start(api, frozenset, door.RefusalLog(tmp_path / "l"), None, every=0.05))
        await asyncio.sleep(0.3)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        return api.asked

    assert asyncio.run(scenario()) > 2, "it kept asking while it ran"
    assert listened == [("127.0.0.1", 8448), ("127.0.0.1", 2586)]


def test_the_routes_are_synapses_and_ntfys():
    assert (door.Route(8448, 8008), door.Route(2586, 2586)) == door.ROUTES
    assert door.WHOIS_TIMEOUT == 2.0
    assert door.CHECK_EVERY == 60.0
    assert door_list.FILE == "door.toml"


def test_the_user_unit_runs_the_door_as_the_owner_and_writes_only_its_log():
    import configparser
    import pathlib

    from messages_mcp import cli

    unit = configparser.ConfigParser(strict=False, interpolation=None)
    unit.read(pathlib.Path(__file__).parent.parent / "systemd/matrix-door.service")
    service = unit["Service"]
    assert service["ExecStart"].split()[1:] == ["door"]
    assert "door" in cli.COMMANDS
    assert service["UMask"] == "0077"
    assert service["ProtectSystem"] == "strict"
    assert service["ReadWritePaths"] == "%h/.local/state/messages-mcp"
    assert "User" not in service, "a user unit runs as its owner"


def test_a_line_the_log_cannot_date_is_dropped(tmp_path):
    path = tmp_path / "door.log"
    path.write_text("not a date\t-\t-\twhois failed\n")
    door.RefusalLog(path).write(door.Verdict(allowed=False, reason="tagged"))
    assert [line.split("\t")[3] for line in path.read_text().splitlines()] == ["tagged"]


async def _through_a_listed_door(place, client, *, tls=None, upstream_port=None):
    """Run `client(host, port)` against a door that lets PHONE in; give what the service got."""
    api = FakeTailscaled({"/localapi/v0/whois": (200, {"Node": PHONE})})
    unix = await asyncio.start_unix_server(api.handle, "api.sock")
    synapse = Upstream(b"synapse")
    up = await asyncio.start_server(synapse.handle, "127.0.0.1", 0)
    port = upstream_port or up.sockets[0].getsockname()[1]
    gate = door.Door(LocalAPI("api.sock", 1).whois, _list(place), door.RefusalLog(place / "log"), tls)
    listener = gate.listen("127.0.0.1", 0)
    serving = asyncio.create_task(gate.serve([(listener, port)]))
    try:
        async with asyncio.timeout(WITHIN):
            answer = await client(*listener.getsockname())
            await asyncio.sleep(0.1)
    finally:
        serving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await serving
        unix.close()
        up.close()
    return answer, synapse.got


async def _say_hello(host, port, ssl_context=None):
    reader, writer = await asyncio.open_connection(host, port, ssl=ssl_context)
    writer.write(b"hello")
    try:
        return await reader.read(1024)
    except ConnectionResetError:
        return b""
    finally:
        writer.close()


def test_a_caller_that_does_not_speak_tls_is_closed(place):
    cert, key = _certificate(place)
    tls = door.tls_context(cert, key)
    answer, got = asyncio.run(_through_a_listed_door(place, _say_hello, tls=tls))
    assert (answer, got) == (b"", b"")


def test_a_service_that_is_not_running_closes_the_call(place):
    spare = socket.socket()
    spare.bind(("127.0.0.1", 0))
    closed_port = spare.getsockname()[1]
    spare.close()
    answer, got = asyncio.run(_through_a_listed_door(place, _say_hello, upstream_port=closed_port))
    assert (answer, got) == (b"", b"")


def test_a_caller_that_resets_ends_its_call_quietly(place):
    async def resetting(host, port):
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(b"hello")
        await reader.read(1024)
        sock = writer.get_extra_info("socket")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        writer.transport.abort()
        return b"reset"

    answer, got = asyncio.run(_through_a_listed_door(place, resetting))
    assert (answer, got) == (b"reset", b"hello")


def test_a_listed_device_calling_over_ipv6_gets_in(place):
    """The door listens on the laptop's IPv6 tailnet address too, and asks about the caller there."""
    back, got, lines, asked = _run(place, {"/localapi/v0/whois": (200, {"Node": PHONE})}, _list(place), host="::1")
    assert got.startswith(b"GET /_matrix/client/versions")
    assert back.startswith(b"synapse: GET")
    assert lines == []
    (addr,) = urllib.parse.parse_qs(urllib.parse.urlsplit(asked[0]).query)["addr"]
    assert addr.startswith("[::1]:")


def test_a_caller_that_keeps_knocking_gets_one_line_a_minute(tmp_path):
    """A looping device is counted, not written each time, so it can neither flood the log nor the disk."""
    clock = [dt.datetime(2026, 11, 18, 12, tzinfo=dt.UTC)]
    log = door.RefusalLog(tmp_path / "door.log", now=lambda: clock[0])
    knocking = door.Verdict(
        allowed=False, reason="not on the list", name="made-up-laptop", stable_id="nFAKE0OTHER0CNTRL"
    )
    other = door.Verdict(allowed=False, reason="whois failed")
    for _ in range(50):
        log.write(knocking)
        clock[0] += dt.timedelta(seconds=1)
    log.write(other)
    clock[0] += dt.timedelta(seconds=60)
    log.write(knocking)
    reasons = [line.split("\t")[3] for line in log.path.read_text().splitlines()]
    assert reasons == ["not on the list", "whois failed", "not on the list (49 more before this)"]


def test_the_refusal_log_keeps_only_its_newest_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(door, "KEEP_LINES", 3)
    clock = [dt.datetime(2026, 11, 18, 12, tzinfo=dt.UTC)]
    log = door.RefusalLog(tmp_path / "door.log", now=lambda: clock[0])
    for i in range(5):
        log.write(door.Verdict(allowed=False, reason="not on the list", stable_id=f"nFAKE{i}CNTRL"))
    assert [line.split("\t")[2] for line in log.path.read_text().splitlines()] == [f"nFAKE{i}CNTRL" for i in (2, 3, 4)]


def test_a_call_with_no_byte_either_way_for_the_idle_time_is_ended(place):
    async def scenario():
        api = FakeTailscaled({"/localapi/v0/whois": (200, {"Node": PHONE})})
        unix = await asyncio.start_unix_server(api.handle, "api.sock")
        synapse = Upstream(b"synapse")
        up = await asyncio.start_server(synapse.handle, "127.0.0.1", 0)
        gate = door.Door(LocalAPI("api.sock", 1).whois, _list(place), door.RefusalLog(place / "log"), None, idle=0.3)
        listener = gate.listen("127.0.0.1", 0)
        serving = asyncio.create_task(gate.serve([(listener, up.sockets[0].getsockname()[1])]))
        loop = asyncio.get_running_loop()
        try:
            async with asyncio.timeout(WITHIN):
                reader, writer = await asyncio.open_connection(*listener.getsockname())
                writer.write(b"hello")
                first = await reader.read(1024)
                began = loop.time()
                last = await reader.read(1024)
                took = loop.time() - began
                writer.close()
        finally:
            serving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await serving
            unix.close()
            up.close()
        return first, last, took

    first, last, took = asyncio.run(scenario())
    assert (first, last) == (b"synapse: hello", b"")
    assert 0.2 < took < 5, took


def test_a_held_back_count_is_written_when_its_caller_stops_knocking(tmp_path):
    """A caller that knocks a few times and never again still gets its count, with the next line written."""
    clock = [dt.datetime(2026, 11, 18, 12, tzinfo=dt.UTC)]
    log = door.RefusalLog(tmp_path / "door.log", now=lambda: clock[0])
    knocking = door.Verdict(
        allowed=False, reason="not on the list", name="made-up-laptop", stable_id="nFAKE0OTHER0CNTRL"
    )
    for _ in range(3):
        log.write(knocking)
        clock[0] += dt.timedelta(seconds=1)
    clock[0] += dt.timedelta(minutes=5)
    log.write(door.Verdict(allowed=False, reason="whois failed"))
    lines = [line.split("\t") for line in log.path.read_text().splitlines()]
    assert [fields[3] for fields in lines] == [
        "not on the list",
        "not on the list (2 more after this, not written then)",
        "whois failed",
    ]
    assert lines[1][2] == "nFAKE0OTHER0CNTRL"


def test_a_held_back_count_is_written_when_the_door_stops(tmp_path):
    clock = [dt.datetime(2026, 11, 18, 12, tzinfo=dt.UTC)]
    log = door.RefusalLog(tmp_path / "door.log", now=lambda: clock[0])
    for _ in range(2):
        log.write(door.Verdict(allowed=False, reason="tagged", stable_id="nFAKE0TAGGED0ID"))
    log.flush()
    log.flush()
    reasons = [line.split("\t")[3] for line in log.path.read_text().splitlines()]
    assert reasons == ["tagged", "tagged (1 more after this, not written then)"]
