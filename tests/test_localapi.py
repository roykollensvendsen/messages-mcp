"""The local API client: one bounded request each, and every failure an error to refuse on."""

import asyncio
import json
import urllib.parse

import pytest

from messages_mcp import localapi
from messages_mcp.localapi import LocalAPI, LocalAPIError


async def _serving(reply, path="api.sock"):
    async def handle(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        if reply is not None:
            writer.write(reply)
            await writer.drain()
            writer.close()
        else:
            await asyncio.sleep(30)

    return await asyncio.start_unix_server(handle, path)


def _ask(tmp_path, monkeypatch, reply, question, timeout=1.0):
    monkeypatch.chdir(tmp_path)

    async def scenario():
        server = await _serving(reply)
        try:
            async with asyncio.timeout(30):
                return await question(LocalAPI("api.sock", timeout))
        finally:
            server.close()

    return asyncio.run(scenario())


def _ok(body):
    return b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n" + json.dumps(body).encode()


def test_whois_gives_the_node(tmp_path, monkeypatch):
    node = {"StableID": "nFAKE0PHONE0CNTRL", "ComputedName": "made-up-phone"}
    assert _ask(tmp_path, monkeypatch, _ok({"Node": node}), lambda api: api.whois("192.0.2.7", 1)) == node


@pytest.mark.parametrize(
    "reply",
    [b"HTTP/1.0 404 Not Found\r\n\r\nno", _ok({"Node": {}}), _ok([]), b"HTTP/1.0 200 OK\r\n\r\nnot json", b""],
)
def test_an_unusable_answer_is_an_error(tmp_path, monkeypatch, reply):
    with pytest.raises(LocalAPIError):
        _ask(tmp_path, monkeypatch, reply, lambda api: api.whois("192.0.2.7", 1))


def test_a_slow_answer_is_an_error_that_says_so(tmp_path, monkeypatch):
    with pytest.raises(LocalAPIError, match="within 0.2 s") as slow:
        _ask(tmp_path, monkeypatch, None, lambda api: api.whois("192.0.2.7", 1), timeout=0.2)
    assert isinstance(slow.value.__cause__, TimeoutError)


def test_no_tailscaled_is_an_error(tmp_path):
    with pytest.raises(LocalAPIError, match="could not answer"):
        asyncio.run(LocalAPI(tmp_path / "nobody.sock", 1).whois("192.0.2.7", 1))


def test_the_laptops_own_addresses(tmp_path, monkeypatch):
    status = {"Self": {"TailscaleIPs": ["192.0.2.7", "2001:db8::7"]}}
    assert _ask(tmp_path, monkeypatch, _ok(status), lambda api: api.own_addresses()) == ["192.0.2.7", "2001:db8::7"]
    with pytest.raises(LocalAPIError, match="no tailnet address"):
        _ask(tmp_path, monkeypatch, _ok({"Self": {}}), lambda api: api.own_addresses())


def test_the_ports_funnel_opens_and_the_ports_serve_passes_on(tmp_path, monkeypatch):
    config = {
        "AllowFunnel": {"made-up.example:8448": True, "made-up.example:443": False},
        "TCP": {"443": {"HTTPS": True}, "9000": {"TCPForward": "127.0.0.1:2586"}},
        "Web": {"made-up.example:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8008"}, "/x": {"Path": "/"}}}},
    }
    assert _ask(tmp_path, monkeypatch, _ok(config), lambda api: api.exposure()) == ({8448}, {2586, 8008})
    assert _ask(tmp_path, monkeypatch, _ok({}), lambda api: api.exposure()) == (set(), set())
    bare = {"Web": {"made-up.example:443": {"Handlers": {"/": {"Proxy": "8008"}, "/y": {"Proxy": "http://x:bad"}}}}}
    assert _ask(tmp_path, monkeypatch, _ok(bare), lambda api: api.exposure()) == (set(), {8008})


def test_an_answer_from_tailscaled_is_read_to_its_end_however_it_is_split(tmp_path, monkeypatch):
    """The real tailscaled sends a big answer in several writes; the client waits for all of them."""
    monkeypatch.chdir(tmp_path)
    status = {"Self": {"TailscaleIPs": ["192.0.2.7"]}, "Peer": {f"made-up-{i}": {"ID": "x" * 40} for i in range(500)}}
    body = json.dumps(status).encode()

    async def split(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        for piece in (b"HTTP/1.0 200 OK\r\n\r\n", body[: len(body) // 2], body[len(body) // 2 :]):
            writer.write(piece)
            await writer.drain()
            await asyncio.sleep(0.05)
        writer.close()

    async def scenario():
        server = await asyncio.start_unix_server(split, "api.sock")
        try:
            async with asyncio.timeout(30):
                return await LocalAPI("api.sock", 5).own_addresses()
        finally:
            server.close()

    assert asyncio.run(scenario()) == ["192.0.2.7"]


def test_an_answer_bigger_than_any_tailscaled_gives_is_refused(tmp_path, monkeypatch):
    huge = _ok({"Node": {"StableID": "nFAKE0PHONE0CNTRL", "padding": "x" * (localapi.MOST + 1)}})
    with pytest.raises(LocalAPIError, match="over"):
        _ask(tmp_path, monkeypatch, huge, lambda api: api.whois("192.0.2.7", 1), timeout=5)


def test_whois_names_an_ipv6_caller_in_brackets(tmp_path, monkeypatch):
    asked = []
    monkeypatch.chdir(tmp_path)

    async def handle(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        asked.append(request.split(b" ")[1].decode())
        writer.write(_ok({"Node": {"StableID": "nFAKE0PHONE0CNTRL"}}))
        await writer.drain()
        writer.close()

    async def scenario():
        server = await asyncio.start_unix_server(handle, "api.sock")
        try:
            api = LocalAPI("api.sock", 5)
            await api.whois("2001:db8::7", 41641)
            await api.whois("192.0.2.7", 41641)
        finally:
            server.close()

    asyncio.run(scenario())
    queries = [urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["addr"] for path in asked]
    assert queries == [["[2001:db8::7]:41641"], ["192.0.2.7:41641"]]
    assert "[" not in asked[0], "the address is encoded in the query"
