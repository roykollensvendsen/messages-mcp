"""The settings we give Synapse and the bridge say what the design promises, key by key."""

import pathlib

import yaml

CONFIG = pathlib.Path(__file__).parent.parent / "config"


def _synapse():
    return yaml.safe_load((CONFIG / "synapse/messages-mcp.yaml").read_text())


def _bridge():
    return yaml.safe_load((CONFIG / "mautrix-meta/messages-mcp.yaml").read_text())


def test_synapse_listens_on_this_laptop_only():
    listeners = _synapse()["listeners"]
    assert [(entry["port"], entry["bind_addresses"]) for entry in listeners] == [(8008, ["127.0.0.1"])]
    assert [resource["names"] for resource in listeners[0]["resources"]] == [["client"]]


def test_a_push_carries_no_message_text():
    assert _synapse()["push"]["include_content"] is False


def test_nobody_sees_the_owner_as_online():
    assert _synapse()["presence"]["enabled"] is False
    assert _bridge()["network"]["send_presence_on_typing"] is False


def test_messages_go_after_thirty_days():
    retention = _synapse()["retention"]
    assert retention["enabled"] is True
    assert retention["default_policy"]["max_lifetime"] == "30d"
    assert retention["allowed_lifetime_max"] == "30d"
    assert retention["purge_jobs"] == [{"interval": "1d"}]


def test_synapse_talks_to_no_other_server_and_lets_nobody_sign_up():
    synapse = _synapse()
    assert synapse["federation_domain_whitelist"] == [], "an empty list, not a missing one, denies all"
    assert synapse["enable_registration"] is False
    assert synapse["report_stats"] is False
    assert synapse["url_preview_enabled"] is False
    assert synapse["allow_public_rooms_over_federation"] is False
    assert synapse["allow_public_rooms_without_auth"] is False
    # Synapse refuses to start with the second without the first.
    assert synapse["limit_profile_requests_to_users_who_share_rooms"] is True
    assert synapse["require_auth_for_profile_requests"] is True


def test_the_bridge_pulls_in_no_old_history():
    bridge = _bridge()
    backfill = bridge["backfill"]
    assert backfill["enabled"] is False
    assert backfill["max_initial_messages"] == 0
    assert backfill["max_catchup_messages"] == 0
    assert backfill["threads"]["max_initial_messages"] == 0
    assert backfill["queue"]["enabled"] is False
    assert bridge["network"]["e2ee_backfill"] is False
    assert bridge["network"]["thread_backfill"]["batch_count"] == 0


def test_the_bridge_logs_warnings_only_and_reports_to_nobody():
    bridge = _bridge()
    assert bridge["logging"]["min_level"] == "warn"
    assert bridge["analytics"]["token"] is None
    assert bridge["homeserver"]["status_endpoint"] is None
    assert bridge["homeserver"]["message_send_checkpoint_endpoint"] is None
    assert bridge["matrix"]["delivery_receipts"] is False
    assert bridge["matrix"]["message_status_events"] is False


def test_the_bridge_talks_to_synapse_on_this_laptop_and_listens_here_only():
    bridge = _bridge()
    assert bridge["homeserver"]["address"] == "http://127.0.0.1:8008"
    assert bridge["appservice"]["hostname"] == "127.0.0.1"
    assert bridge["bridge"]["bridge_matrix_leave"] is False


def test_the_bridge_makes_no_encrypted_rooms():
    """No Matrix-side encryption in v1, the owner's choice B (18.11); turning it on later is D-enc."""
    encryption = _bridge()["encryption"]
    assert encryption["allow"] is False
    assert encryption["default"] is False


def test_the_bridge_keeps_ephemeral_events_on_until_measured():
    """What turning it off breaks is measured with a decoy account first (slice M12)."""
    assert _bridge()["appservice"]["ephemeral_events"] is True


def test_the_bridge_has_no_relay():
    """A relay would let others talk through the owner's account (voice-architect f2 on #4)."""
    relay = _bridge()["bridge"]["relay"]
    assert relay["enabled"] is False
    assert relay["default_relays"] == []


def test_ntfy_lets_nobody_in_without_an_entry_of_their_own():
    ntfy = yaml.safe_load((CONFIG / "ntfy/server.yml").read_text())
    assert ntfy["auth-default-access"] == "deny-all"
    assert "auth-file" not in ntfy, "the auth file's place is filled in on the laptop, never committed"
