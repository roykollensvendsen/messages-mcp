"""ntfy's config on this laptop: our settings, with what only the laptop knows filled in.

That is its base-url, the door's address for ntfy (the Matrix gateway needs it), and the place
of its user list. The list holds the phone's password hash, so its folder is the owner's alone,
and the unit's UMask keeps the file 0600. Nothing here ever writes the list itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from messages_mcp import bridge_config

if TYPE_CHECKING:
    import pathlib

#: The door's port for ntfy.
PORT = 2586


class NtfyConfigError(Exception):
    """ntfy's config was not written; the message says why."""


def apply(ours: pathlib.Path, config: pathlib.Path, name: str, state: pathlib.Path) -> None:
    """Write `ours` to `config`, for the laptop `name`, with the user list under `state`."""
    try:
        wanted = yaml.safe_load(ours.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as broken:
        msg = f"{ours} cannot be read ({broken})"
        raise NtfyConfigError(msg) from broken
    if not isinstance(wanted, dict):
        msg = f"{ours} is not ntfy settings"
        raise NtfyConfigError(msg)
    users = state / "ntfy"
    # RULE: ntfy keeps its user list where only the owner reads it
    users.mkdir(mode=0o700, parents=True, exist_ok=True)
    users.chmod(0o700)
    # RULE: ntfy is addressed through the door for this laptop
    filled = {**wanted, "base-url": f"https://{name}:{PORT}", "auth-file": str(users / "user.db")}
    config.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    bridge_config.write_private(config, yaml.safe_dump(filled, sort_keys=False))
