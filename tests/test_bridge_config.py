"""Our settings go into the bridge's own config: ours win, the bridge's others stay, and only the owner reads it."""

import pathlib

import pytest
import yaml

from messages_mcp import bridge_config

NAME = "made-up-laptop.example.invalid"
OURS = pathlib.Path(__file__).parent.parent / "config/mautrix-meta/messages-mcp.yaml"
#: A made-up stand-in for the config the bridge generates, with its example values.
GENERATED = {
    "network": {"mode": "messenger", "send_presence_on_typing": True, "e2ee_backfill": True},
    "homeserver": {"address": "http://example.localhost:8008", "domain": "example.com", "software": "standard"},
    "appservice": {"as_token": "made-up-as-token", "hs_token": "made-up-hs-token", "ephemeral_events": True},
    "bridge": {
        "command_prefix": "!meta",
        "permissions": {"*": "relay", "example.com": "user", "@admin:example.com": "admin"},
    },
    "backfill": {"enabled": True, "max_initial_messages": 50, "queue": {"enabled": True, "batch_size": 100}},
    "logging": {"min_level": "debug", "writers": [{"type": "stdout"}]},
}


def _merged(tmp_path, generated=GENERATED, mode=0o600):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(generated))
    path.chmod(mode)
    bridge_config.apply(OURS, path, NAME)
    return path, yaml.safe_load(path.read_text())


def _leaves(tree, prefix=()):
    for key, value in tree.items():
        if isinstance(value, dict) and value:
            yield from _leaves(value, (*prefix, key))
        else:
            yield (*prefix, key), value


def _at(tree, keys):
    for key in keys:
        tree = tree[key]
    return tree


def test_our_settings_win_over_the_ones_the_bridge_made(tmp_path):
    _, merged = _merged(tmp_path)
    ours = yaml.safe_load(OURS.read_text())
    for keys, value in _leaves(ours):
        assert _at(merged, keys) == value, keys


def test_the_bridges_other_settings_are_kept(tmp_path):
    _, merged = _merged(tmp_path)
    assert merged["network"]["mode"] == "messenger"
    assert merged["appservice"]["as_token"] == "made-up-as-token"  # noqa: S105 - made up
    assert merged["bridge"]["command_prefix"] == "!meta"
    assert merged["backfill"]["queue"]["batch_size"] == 100
    assert merged["logging"]["writers"] == [{"type": "stdout"}]


def test_only_matrix_users_on_this_laptop_may_use_the_bridge(tmp_path):
    _, merged = _merged(tmp_path)
    assert merged["homeserver"]["domain"] == NAME
    assert merged["bridge"]["permissions"] == {NAME: "user"}


def test_the_bridges_config_stays_readable_by_the_owner_only(tmp_path):
    path, _ = _merged(tmp_path, mode=0o644)
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_config_that_is_not_the_bridges_is_left_alone(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("- not\n- a map\n")
    with pytest.raises(bridge_config.BridgeConfigError, match="not a bridge config"):
        bridge_config.apply(OURS, path, NAME)
    assert path.read_text() == "- not\n- a map\n"
    with pytest.raises(bridge_config.BridgeConfigError, match="cannot be read"):
        bridge_config.apply(OURS, tmp_path / "missing.yaml", NAME)


def test_a_write_that_fails_half_way_leaves_the_old_config(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(GENERATED))
    before = path.read_text()

    def no_room(_self, _target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pathlib.Path, "replace", no_room)
    with pytest.raises(OSError, match="No space"):
        bridge_config.apply(OURS, path, NAME)
    assert path.read_text() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.yaml"], "no half-written file is left"


def test_an_admin_on_this_laptops_server_keeps_that_right(tmp_path):
    """A second run keeps the owner's own admin line, and nothing else from before (voice-architect, #5)."""
    generated = {
        **GENERATED,
        "bridge": {
            "permissions": {
                f"@made-up-owner:{NAME}": "admin",
                f"@made-up-guest:{NAME}": "user",
                "@made-up-admin:elsewhere.invalid": "admin",
                f"@{NAME}": "admin",
                "*": "admin",
            }
        },
    }
    _, merged = _merged(tmp_path, generated)
    assert merged["bridge"]["permissions"] == {NAME: "user", f"@made-up-owner:{NAME}": "admin"}
