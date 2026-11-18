"""The local API client: one bounded request each, and every failure an error to refuse on."""

import asyncio
import json

import pytest

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


def test_the_ports_funnel_opens(tmp_path, monkeypatch):
    config = {"AllowFunnel": {"made-up.example:8448": True, "made-up.example:443": False}}
    assert _ask(tmp_path, monkeypatch, _ok(config), lambda api: api.funnel_ports()) == {8448}
    assert _ask(tmp_path, monkeypatch, _ok({}), lambda api: api.funnel_ports()) == set()
