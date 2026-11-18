"""The plugin's own settings, read from its own config folder.

`claude-voice plugins install` writes `config.default.toml` there the first time, so the file
holds exactly the keys that file has. A key it does not know is refused by name, so a typo
never passes as a default silently.
"""

from __future__ import annotations

import os
import pathlib
import tomllib
from dataclasses import dataclass

from messages_mcp import platform

#: The folder under the config home, as the manifest's `config.dir` names it.
CONFIG_DIR = "messages-mcp"
#: Points at another config file, for a test or a second gizmo.
CONFIG_ENV = "MESSAGES_MCP_CONFIG"


class SettingsError(ValueError):
    """The config file cannot be used; the message names the fix."""


@dataclass(frozen=True)
class Settings:
    """What the plugin reads from its config file."""

    address: str = "gizmo.example.invalid"
    simulate: bool = True
    #: matrix-door's TLS certificate and key, from `tailscale cert`; empty means the config folder's.
    door_cert: str = ""
    door_key: str = ""

    def door_files(self) -> tuple[pathlib.Path, pathlib.Path]:
        """The certificate and key matrix-door serves, with the config folder's as the default."""
        folder = platform.current().config_home() / CONFIG_DIR
        cert = pathlib.Path(self.door_cert).expanduser() if self.door_cert else folder / "door.crt"
        key = pathlib.Path(self.door_key).expanduser() if self.door_key else folder / "door.key"
        return cert, key


def config_file() -> pathlib.Path:
    """Where the config is: `$MESSAGES_MCP_CONFIG`, or the plugin's folder in the config home."""
    named = os.environ.get(CONFIG_ENV)
    return pathlib.Path(named) if named else platform.current().config_home() / CONFIG_DIR / "config.toml"


def load(path: pathlib.Path | None = None) -> Settings:
    """The settings in `path`, or the defaults when there is no file."""
    path = config_file() if path is None else path
    if not path.is_file():
        return Settings()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as broken:
        msg = f"{path} is not valid TOML ({broken}); fix it, or delete it to use the defaults"
        raise SettingsError(msg) from broken
    device = data.pop("device", {})
    door = data.pop("door", {})
    unknown = sorted(data) + _unknown("device", device) + _unknown("door", door)
    if unknown:
        msg = f"{path} has keys messages-mcp does not know: {', '.join(unknown)}; remove them or fix their spelling"
        raise SettingsError(msg)
    return Settings(
        address=str(device.get("address", Settings.address)),
        simulate=bool(device.get("simulate", True)),
        door_cert=str(door.get("cert", "")),
        door_key=str(door.get("key", "")),
    )


#: The keys each table of the config file may hold.
KNOWN = {"device": ("address", "simulate"), "door": ("cert", "key")}


def _unknown(table: str, keys: dict[str, object]) -> list[str]:
    """The keys in `table` the plugin does not know, each named with its table."""
    return sorted(f"{table}.{key}" for key in keys if key not in KNOWN[table])
