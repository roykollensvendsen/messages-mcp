"""The plugin's own settings, read from its own config folder.

`claude-voice plugins install` writes `config.default.toml` there the first time, so the file
holds exactly the keys that file has. A key it does not know is refused by name, so a typo
never passes as a default silently.
"""

from __future__ import annotations

import os
import pathlib
import tomllib
from dataclasses import dataclass, field

from messages_mcp import platform

#: The folder under the config home, as the manifest's `config.dir` names it.
CONFIG_DIR = "messages-mcp"
#: Points at another config file, for a test.
CONFIG_ENV = "MESSAGES_MCP_CONFIG"


class SettingsError(ValueError):
    """The config file cannot be used; the message names the fix."""


@dataclass(frozen=True)
class Settings:
    """What the plugin reads from its config file."""

    #: Answer from the made-up chats in fake.py instead of the real inbox.
    simulate: bool = False
    #: The laptop's own Synapse, reached on loopback, never through the door.
    homeserver: str = "http://127.0.0.1:8008"
    #: The owner's own names for Messenger logins, by the bridge's login ID; private.
    accounts: dict[str, str] = field(default_factory=dict)
    #: matrix-door's TLS certificate and key, from `tailscale cert`; empty means the config folder's.
    door_cert: str = ""
    door_key: str = ""

    def door_files(self) -> tuple[pathlib.Path, pathlib.Path]:
        """The certificate and key matrix-door serves, with the config folder's as the default."""
        folder = platform.current().config_home() / CONFIG_DIR
        cert = pathlib.Path(self.door_cert).expanduser() if self.door_cert else folder / "door.crt"
        key = pathlib.Path(self.door_key).expanduser() if self.door_key else folder / "door.key"
        return cert, key

    def login_file(self) -> pathlib.Path:
        """Where `messages-mcp login` keeps the plugin's own Matrix login, readable by the owner only."""
        return platform.current().config_home() / CONFIG_DIR / "matrix-login.json"


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
    inbox = data.pop("inbox", {})
    door = data.pop("door", {})
    accounts = data.pop("accounts", {})
    unknown = sorted(data) + _unknown("inbox", inbox) + _unknown("door", door)
    if unknown:
        msg = f"{path} has keys messages-mcp does not know: {', '.join(unknown)}; remove them or fix their spelling"
        raise SettingsError(msg)
    return Settings(
        simulate=bool(inbox.get("simulate", False)),
        homeserver=str(inbox.get("homeserver", Settings.homeserver)),
        accounts=_names(accounts),
        door_cert=str(door.get("cert", "")),
        door_key=str(door.get("key", "")),
    )


#: The keys each table of the config file may hold.
KNOWN = {"inbox": ("simulate", "homeserver"), "door": ("cert", "key")}


def _names(table: dict[str, object]) -> dict[str, str]:
    """The owner's names for logins, from the [accounts] table."""
    return {str(login): str(name) for login, name in table.items()}


def _unknown(table: str, keys: dict[str, object]) -> list[str]:
    """The keys in `table` the plugin does not know, each named with its table."""
    return sorted(f"{table}.{key}" for key in keys if key not in KNOWN[table])
