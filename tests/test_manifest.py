"""The install manifest meets claude-voice's published format, and says what the code does.

`tests/plugin-manifest-v1.json` is a copy of `docs/plugin-manifest/v1.json` in claude-voice,
kept here so this check runs offline, without its `$id`. When the bridge publishes a new
version, copy it again.
"""

import json
import pathlib
import re
import sys
import tomllib

import jsonschema
import pytest

from messages_mcp import settings
from messages_mcp.server import TOOLS

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import private_data_scan

SCHEMA = json.loads((ROOT / "tests/plugin-manifest-v1.json").read_text(encoding="utf-8"))


def _manifest():
    return tomllib.loads((ROOT / "claude-voice-plugin.toml").read_text(encoding="utf-8"))


def test_the_manifest_meets_the_published_format():
    jsonschema.validate(_manifest(), SCHEMA)


@pytest.mark.parametrize(
    "change",
    [{"surprise": 1}, {"name": "Not A Name"}, {"tools": []}, {"uses": ["position"]}],
    ids=["an unknown key", "a bad name", "no tools", "an unknown shared value"],
)
def test_the_format_refuses_what_install_would_refuse(change):
    """Proof the check above can fail: each of these is refused by name in the bridge too."""
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**_manifest(), **change}, SCHEMA)


def test_the_manifest_lists_exactly_the_tools_the_server_offers():
    assert sorted(_manifest()["tools"]) == sorted(TOOLS)


def test_the_manifest_starts_the_command_this_package_installs():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = _manifest()
    assert manifest["args"][-1] == "serve"
    assert manifest["args"][-2] in pyproject["project"]["scripts"]


def test_the_manifest_names_the_config_folder_the_plugin_reads():
    config = _manifest()["config"]
    assert config["dir"] == settings.CONFIG_DIR
    assert (ROOT / config["default"]).is_file()


def test_the_default_config_holds_made_up_values_only():
    text = (ROOT / _manifest()["config"]["default"]).read_text(encoding="utf-8")
    assert private_data_scan.offences(text, set()) == []
    hosts = re.findall(r"https?://([^/:\"]+)", text)
    assert hosts, "the default names the laptop's own Synapse"
    for host in hosts:
        assert host == "127.0.0.1" or host.endswith(".invalid"), f"{host}: only this laptop, or a made-up .invalid host"


def test_the_template_s_gizmo_is_gone():
    """Quality f1 on #1: nothing the owner installs or reads still speaks of the made-up gizmo."""
    shipped = [
        *sorted((ROOT / "src").rglob("*.py")),
        ROOT / "config.default.toml",
        ROOT / "README.md",
        ROOT / "docs" / "setup.md",
    ]
    assert [path.name for path in shipped if "gizmo" in path.read_text(encoding="utf-8").lower()] == []
