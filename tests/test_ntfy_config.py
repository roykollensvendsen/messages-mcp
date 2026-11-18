"""ntfy's config on the laptop: our settings, its public address, and its user list, kept by the owner alone."""

import pathlib
import subprocess

import pytest
import yaml

from messages_mcp import ntfy_config

NAME = "made-up-laptop.example.invalid"
OURS = pathlib.Path(__file__).parent.parent / "config/ntfy/server.yml"


def _written(tmp_path):
    target = tmp_path / "config" / "ntfy" / "server.yml"
    ntfy_config.apply(OURS, target, NAME, tmp_path / "state")
    return target, yaml.safe_load(target.read_text())


def test_ntfy_is_addressed_through_the_door_for_this_laptop(tmp_path):
    _, written = _written(tmp_path)
    assert written["base-url"] == f"https://{NAME}:2586"
    assert written["auth-default-access"] == "deny-all"


def test_ntfy_keeps_its_user_list_where_only_the_owner_reads_it(tmp_path):
    target, written = _written(tmp_path)
    users = pathlib.Path(written["auth-file"])
    assert users == tmp_path / "state" / "ntfy" / "user.db"
    assert users.parent.stat().st_mode & 0o777 == 0o700
    assert target.stat().st_mode & 0o777 == 0o600


def test_no_user_list_is_ever_committed():
    tracked = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    assert not [path for path in tracked if path.endswith((".db", ".db-wal", ".db-shm"))]


def test_settings_that_are_not_a_map_are_refused(tmp_path):
    ours = tmp_path / "ours.yml"
    ours.write_text("- not a map\n")
    with pytest.raises(ntfy_config.NtfyConfigError, match="not ntfy settings"):
        ntfy_config.apply(ours, tmp_path / "server.yml", NAME, tmp_path)
    with pytest.raises(ntfy_config.NtfyConfigError, match="cannot be read"):
        ntfy_config.apply(tmp_path / "missing.yml", tmp_path / "server.yml", NAME, tmp_path)
