"""The user units: each runs as the owner, writes only its own folder, and in the order the setup needs."""

import configparser
import pathlib

import pytest

from messages_mcp import cli

UNITS = pathlib.Path(__file__).parent.parent / "systemd"
SERVICES = ["synapse", "ntfy", "mautrix-meta", "matrix-door-cert"]


def _unit(name):
    unit = configparser.ConfigParser(strict=False, interpolation=None)
    unit.optionxform = str
    assert unit.read(UNITS / name), name
    return unit


@pytest.mark.parametrize("name", SERVICES)
def test_each_service_runs_as_the_owner_and_writes_only_its_own_folder(name):
    service = _unit(f"{name}.service")["Service"]
    assert "User" not in service, "a user unit runs as its owner"
    assert service["UMask"] == "0077"
    assert service["NoNewPrivileges"] == "yes"
    assert service["ProtectSystem"] == "strict"
    writes = service.get("ReadWritePaths", "").split()
    assert all(path.startswith("%h/") for path in writes)


def test_synapse_reads_our_settings_after_its_own_and_the_bridges_last():
    command = _unit("synapse.service")["Service"]["ExecStart"].split()
    files = [command[i + 1] for i, word in enumerate(command) if word == "-c"]
    assert files == [
        "%h/.local/share/synapse/homeserver.yaml",
        "%h/.local/share/synapse/messages-mcp.yaml",
        "%h/.local/share/synapse/bridge.yaml",
    ]


def test_ntfy_listens_on_this_laptop_only():
    command = _unit("ntfy.service")["Service"]["ExecStart"].split()
    assert command[command.index("--listen-http") + 1] == "127.0.0.1:2586"


def test_ntfy_reads_the_config_made_for_this_laptop_and_writes_only_its_user_list():
    service = _unit("ntfy.service")["Service"]
    command = service["ExecStart"].split()
    assert command[command.index("--config") + 1] == "%h/.config/ntfy/server.yml"
    assert service["ReadWritePaths"] == "%h/.local/state/ntfy"


def test_the_bridge_starts_after_synapse_and_needs_it():
    unit = _unit("mautrix-meta.service")["Unit"]
    assert "synapse.service" in unit["After"].split()
    assert unit["Requires"] == "synapse.service"


def test_the_doors_certificate_is_renewed_each_week_and_the_door_restarted():
    service = _unit("matrix-door-cert.service")["Service"]
    assert service["Type"] == "oneshot"
    assert service["ExecStart"].split()[1:] == ["door-cert"]
    assert "door-cert" in cli.COMMANDS
    assert service["ExecStartPost"].split()[-2:] == ["try-restart", "matrix-door.service"]
    assert service["ReadWritePaths"] == "%h/.config/messages-mcp"
    timer = _unit("matrix-door-cert.timer")["Timer"]
    assert timer["OnCalendar"] == "weekly"
    assert timer["Persistent"] == "true"
