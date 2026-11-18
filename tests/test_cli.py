"""The command line says what the voice would hear, and a broken config says how to fix it."""

import pytest

from messages_mcp import cli


@pytest.mark.usefixtures("own_folders")
def test_unread_says_what_the_voice_would_hear(capsys):
    assert cli.main(["unread", "--simulate"]) == 0
    said = capsys.readouterr().out
    assert said.startswith("Simulated: 2 chats with new messages. “Made-up Friend”: 2 new; ")


@pytest.mark.usefixtures("own_folders")
def test_with_no_login_unread_says_so(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[inbox]\nsimulate = false\n")
    monkeypatch.setenv("MESSAGES_MCP_CONFIG", str(config))
    assert cli.main(["unread"]) == 0
    assert capsys.readouterr().out == "messages-mcp isn't logged in to the inbox yet, so nothing was read.\n"


@pytest.mark.usefixtures("own_folders")
def test_a_simulated_read_numbers_its_made_up_logins_apart_from_the_real_ones(tmp_path):
    cli.main(["unread", "--simulate"])
    state = tmp_path / "state" / "messages-mcp"
    assert (state / "accounts-simulated.json").exists()
    assert not (state / "accounts.json").exists()


@pytest.mark.usefixtures("own_folders")
def test_login_asks_for_the_user_and_a_hidden_password_and_says_where_it_is_kept(monkeypatch, capsys):
    asked = {}

    async def log_in(homeserver, user, password, path):
        asked.update(homeserver=homeserver, user=user, password=password, path=path)
        return "MADEUPDEVICE"

    monkeypatch.setattr("builtins.input", lambda _prompt: " @owner:made-up.example.invalid ")
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "made-up-password")
    monkeypatch.setattr(cli.login, "log_in", log_in)
    assert cli.main(["login"]) == 0
    assert asked["user"] == "@owner:made-up.example.invalid"
    assert asked["homeserver"] == "http://127.0.0.1:8008"
    assert asked["path"].name == "matrix-login.json"
    said = capsys.readouterr().out
    assert "MADEUPDEVICE" in said
    assert "made-up-password" not in said


@pytest.mark.usefixtures("own_folders")
def test_a_refused_login_says_why_and_fails(monkeypatch, capsys):
    async def log_in(*_):
        msg = "Synapse refused the login (made up)"
        raise cli.login.LoginError(msg)

    monkeypatch.setattr("builtins.input", lambda _prompt: "@owner:made-up.example.invalid")
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "made-up-wrong")
    monkeypatch.setattr(cli.login, "log_in", log_in)
    assert cli.main(["login"]) == 1
    assert capsys.readouterr().err == "not logged in: Synapse refused the login (made up)\n"


def test_a_broken_config_stops_the_command_with_the_fix(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[inbox\n")
    monkeypatch.setenv("MESSAGES_MCP_CONFIG", str(config))
    assert cli.main(["unread"]) == 2
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


@pytest.mark.usefixtures("own_folders")
def test_renewing_the_doors_certificate_with_no_tailscaled_says_so(capsys):
    assert cli.main(["door-cert"]) == 1
    assert "not renewed" in capsys.readouterr().err


def test_the_bridge_gets_our_settings_for_this_laptops_name(own_folders, monkeypatch, capsys):
    import pathlib

    import yaml

    async def own_name(_self):
        return "made-up-laptop.example.invalid"

    monkeypatch.setattr(cli.LocalAPI, "own_name", own_name)
    config = own_folders / "bridge.yaml"
    config.write_text("appservice:\n  as_token: made-up-as-token\n")
    ours = pathlib.Path(__file__).parent.parent / "config/mautrix-meta/messages-mcp.yaml"
    assert cli.main(["bridge-config", str(ours), str(config)]) == 0
    merged = yaml.safe_load(config.read_text())
    assert merged["homeserver"]["domain"] == "made-up-laptop.example.invalid"
    out = capsys.readouterr()
    assert "made-up-as-token" not in out.out + out.err, "the bridge's tokens are never printed"


def test_the_bridge_config_with_no_tailscaled_is_left_alone(own_folders, capsys):
    config = own_folders / "bridge.yaml"
    config.write_text("appservice: {}\n")
    assert cli.main(["bridge-config", "ours.yaml", str(config)]) == 1
    assert "left as it was" in capsys.readouterr().err
    assert config.read_text() == "appservice: {}\n"


def test_ntfy_gets_its_config_for_this_laptops_name(own_folders, monkeypatch):
    import pathlib

    import yaml

    async def own_name(_self):
        return "made-up-laptop.example.invalid"

    monkeypatch.setattr(cli.LocalAPI, "own_name", own_name)
    ours = pathlib.Path(__file__).parent.parent / "config/ntfy/server.yml"
    target = own_folders / "ntfy.yml"
    assert cli.main(["ntfy-config", str(ours), str(target)]) == 0
    assert yaml.safe_load(target.read_text())["base-url"] == "https://made-up-laptop.example.invalid:2586"


def test_ntfys_config_with_no_tailscaled_is_not_written(own_folders, capsys):
    assert cli.main(["ntfy-config", "ours.yml", str(own_folders / "ntfy.yml")]) == 1
    assert "not written" in capsys.readouterr().err
    assert not (own_folders / "ntfy.yml").exists()
