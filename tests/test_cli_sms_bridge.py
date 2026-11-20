"""`messages-mcp sms-bridge`: write its registration, or run it, and stop at once when not set up."""

from __future__ import annotations

import argparse
import asyncio
import json
import stat

import pytest
import yaml

from messages_mcp import cli, settings
from messages_mcp.device import NotSetUpError
from messages_mcp.sms_bridge import SmsBridge, _until
from messages_mcp.sms_hub import ToolAnswer
from messages_mcp.sms_rooms import write_registration

SERVER = "made-up-laptop.example.invalid"
OWNER = f"@owner:{SERVER}"


@pytest.fixture
def own_folders(tmp_path, monkeypatch):
    """Every folder sms-bridge could read or write, in the test's own folder."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MESSAGES_MCP_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(cli.platform.linux.Linux, "tailscaled_socket", lambda _self: tmp_path / "nobody.sock")
    return tmp_path


class Key:
    def key(self):
        return "made-up-plugin-key"


class Bridge:
    async def call(self, _tool, _arguments, _key):
        return ToolAnswer(is_error=False, structured={})


def _set_up(folders, *, phone="phone-a"):
    """A config naming the phone, the plugin's login, and the bridge's registration."""
    (folders / "config.toml").write_text(f'[sms]\nphone = "{phone}"\n', encoding="utf-8")
    login = folders / "config" / "messages-mcp" / "matrix-login.json"
    login.parent.mkdir(parents=True)
    login.write_text(
        json.dumps({"homeserver": "http://127.0.0.1:8008", "access_token": "made-up", "user_id": OWNER}),
        encoding="utf-8",
    )
    write_registration(folders / "data" / "sms-bridge" / "registration.yaml", SERVER)


def _run(**parts):
    return cli.run_sms_bridge(argparse.Namespace(action="run"), settings.load(), **parts)


def test_sms_bridge_without_its_own_key_stops_at_its_start(own_folders, capsys):
    """Until plugin-callback-v1 is approved, the key source refuses, and the unit does not restart."""
    _set_up(own_folders)
    assert cli.main(["sms-bridge", "run"]) == cli.NOT_SET_UP == 2
    said = capsys.readouterr().err
    assert "plugin-callback-v1" in said
    assert "never used" in said


def test_sms_bridge_with_a_key_but_no_client_yet_stops_at_its_start(own_folders, capsys):
    _set_up(own_folders)
    assert _run(keys=Key()) == 2
    assert "no client for the claude-voice bridge yet" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("missing", "said"),
    [
        ("phone", "[sms] phone"),
        ("registration", "no usable registration"),
        ("login", "messages-mcp login"),
    ],
)
def test_sms_bridge_missing_a_part_stops_at_its_start_naming_it(own_folders, capsys, missing, said):
    _set_up(own_folders, phone="" if missing == "phone" else "phone-a")
    if missing == "registration":
        (own_folders / "data" / "sms-bridge" / "registration.yaml").unlink()
    if missing == "login":
        (own_folders / "config" / "messages-mcp" / "matrix-login.json").unlink()
    assert _run(keys=Key(), caller=Bridge()) == 2
    assert said in capsys.readouterr().err


def test_sms_bridge_set_up_is_put_together_from_its_own_folders(own_folders):
    _set_up(own_folders)
    data, state = own_folders / "data" / "sms-bridge", own_folders / "state" / "sms-bridge"
    bridge = cli.sms_bridge_parts(settings.load(), data, state, Key(), Bridge())
    assert isinstance(bridge, SmsBridge)
    assert bridge.hub.tool == "phone-a_sms_since"
    assert bridge.rooms.owner == OWNER
    assert bridge.rooms.rooms_file == data / "rooms.json"
    assert bridge.position == state / "position.json"


def test_the_key_is_asked_for_before_anything_else(own_folders):
    """With nothing else set up, a missing key is still what is said first."""

    class Refuses:
        def key(self):
            msg = "made-up: no key"
            raise NotSetUpError(msg)

    with pytest.raises(NotSetUpError, match="no key"):
        cli.sms_bridge_parts(settings.load(), own_folders / "data", own_folders / "state", Refuses(), Bridge())


def test_the_registration_is_written_for_this_laptop_s_matrix_server(own_folders, monkeypatch, capsys):
    async def own_name(_self):
        return SERVER

    monkeypatch.setattr(cli.LocalAPI, "own_name", own_name)
    assert cli.main(["sms-bridge", "registration"]) == 0
    path = own_folders / "data" / "sms-bridge" / "registration.yaml"
    made = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert made["namespaces"]["users"][0]["regex"].endswith(r"made\-up\-laptop\.example\.invalid")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    out = capsys.readouterr()
    assert made["as_token"] not in out.out + out.err, "the bridge's tokens are never printed"
    assert cli.main(["sms-bridge", "registration"]) == 0
    assert "is kept" in capsys.readouterr().out
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == made


def test_the_registration_with_no_tailscaled_is_not_written(own_folders, capsys):
    assert cli.main(["sms-bridge", "registration"]) == 1
    assert "no registration written" in capsys.readouterr().err
    assert not (own_folders / "data" / "sms-bridge" / "registration.yaml").exists()


def test_a_stop_ends_the_bridge_s_wait_at_once():
    async def go():
        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(_until(stop, 120), 5)

    asyncio.run(go())


def test_the_bridge_s_wait_runs_its_time_without_a_stop():
    async def go():
        loop = asyncio.get_running_loop()
        started = loop.time()
        await _until(asyncio.Event(), 0.05)
        return loop.time() - started

    assert asyncio.run(go()) >= 0.04


def test_run_waits_on_the_stop_when_given_no_sleep(tmp_path):
    class Hub:
        async def sms_since(self, _cursor):
            return {"messages": [], "cursor": "c-1", "more": False, "dropped": 0, "reset": False}

    class Rooms:
        async def post(self, _sms):
            pass

        async def notice(self, _words):
            pass

    async def go():
        stop = asyncio.Event()
        bridge = SmsBridge(Hub(), Rooms(), tmp_path / "position.json")
        running = asyncio.create_task(bridge.run(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(running, 5)
        return bridge.empty_polls

    assert asyncio.run(go()) == 1


@pytest.mark.parametrize("kept", ["[]", "{not json"])
def test_a_broken_room_list_stops_sms_bridge_at_its_start(own_folders, capsys, kept):
    """Polling on would only fail each text; exit 2 names the file, and the unit is not restarted."""
    _set_up(own_folders)
    (own_folders / "data" / "sms-bridge" / "rooms.json").write_text(kept, encoding="utf-8")
    assert _run(keys=Key(), caller=Bridge()) == 2
    assert "rooms.json" in capsys.readouterr().err
