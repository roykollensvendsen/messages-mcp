"""The plugin's own Matrix login: kept for the owner only, with no password in it."""

import asyncio
import json

import pytest
from homeserver import OWNER, PASSWORD, PLUGIN_TOKEN, serving

from messages_mcp import login


def _log_in(path, user=OWNER, password=PASSWORD):
    async def scenario():
        async with serving() as server:
            return await login.log_in(server.url, user, password, path), server

    return asyncio.run(scenario())


def test_the_plugins_login_is_readable_by_the_owner_only(tmp_path):
    path = tmp_path / "messages-mcp" / "matrix-login.json"
    device, server = _log_in(path)
    assert device == "MADEUPDEVICE"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    kept = json.loads(path.read_text())
    assert kept == {
        "homeserver": server.url,
        "user_id": OWNER,
        "device_id": "MADEUPDEVICE",
        "access_token": PLUGIN_TOKEN,
    }


def test_the_password_is_never_kept(tmp_path):
    path = tmp_path / "matrix-login.json"
    _log_in(path)
    assert PASSWORD not in path.read_text()


def test_the_device_is_named_so_the_owner_knows_it_in_element(tmp_path):
    _, server = _log_in(tmp_path / "matrix-login.json")
    assert [(m, p) for m, p, _ in server.asked_by("")] == [("POST", "/_matrix/client/v3/login")]


def test_a_wrong_password_is_refused_and_nothing_is_kept(tmp_path):
    path = tmp_path / "matrix-login.json"
    with pytest.raises(login.LoginError, match="refused"):
        _log_in(path, password="made-up-wrong")  # noqa: S106 - made up
    assert not path.exists()


def test_a_synapse_that_is_down_is_said(tmp_path):
    path = tmp_path / "matrix-login.json"
    with pytest.raises(login.LoginError, match="did not answer"):
        asyncio.run(login.log_in("http://127.0.0.1:9", OWNER, PASSWORD, path))
    assert not path.exists()


def test_a_synapse_that_is_not_on_this_laptop_gets_no_password(tmp_path):
    path = tmp_path / "matrix-login.json"
    with pytest.raises(login.LoginError, match="not on this laptop"):
        asyncio.run(login.log_in("https://made-up.example.invalid", OWNER, PASSWORD, path))
    assert not path.exists()
