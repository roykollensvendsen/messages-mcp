"""The command line says what the voice would hear, and a broken config says how to fix it."""

import pytest

from messages_mcp import cli


def test_status_says_what_the_voice_would_hear(capsys):
    assert cli.main(["status", "--simulate"]) == 0
    assert capsys.readouterr().out == "Simulated: The gizmo is on, at level 40.\n"


def test_with_no_real_gizmo_set_up_status_says_so(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[device]\nsimulate = false\n")
    monkeypatch.setenv("MESSAGES_MCP_CONFIG", str(config))
    assert cli.main(["status"]) == 0
    assert capsys.readouterr().out == "No real gizmo is set up yet, so nothing was asked.\n"


def test_a_broken_config_stops_the_command_with_the_fix(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[device\n")
    monkeypatch.setenv("MESSAGES_MCP_CONFIG", str(config))
    assert cli.main(["status"]) == 2
    assert "delete it to use the defaults" in capsys.readouterr().err


def test_every_verb_is_one_the_command_takes():
    for verb in cli.COMMANDS:
        with pytest.raises(SystemExit) as shown:
            cli.main([verb, "--help"])
        assert shown.value.code == 0


@pytest.fixture
def own_folders(tmp_path, monkeypatch):
    """Every folder the door could read or write, in the test's own folder."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("MESSAGES_MCP_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(cli.platform.linux.Linux, "tailscaled_socket", lambda _self: tmp_path / "nobody.sock")
    return tmp_path


@pytest.mark.usefixtures("own_folders")
def test_the_door_with_no_certificate_says_which_step_makes_one(capsys):
    assert cli.main(["door"]) == 1
    assert "step C" in capsys.readouterr().err


def test_the_door_with_no_tailscaled_will_not_start(own_folders, capsys):
    from test_door import _certificate

    cert, key = _certificate(own_folders)
    (own_folders / "config.toml").write_text(f'[door]\ncert = "{cert}"\nkey = "{key}"\n')
    assert cli.main(["door"]) == 1
    assert "matrix-door cannot run" in capsys.readouterr().err
