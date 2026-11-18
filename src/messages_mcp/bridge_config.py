"""Putting messages-mcp's settings into the config mautrix-meta generates for itself.

The bridge reads one config file only, so ours are merged into it: each of our keys wins, a key
of ours that holds a map is merged key by key, and every other setting of the bridge's stays.
The homeserver is the laptop's own, by its tailnet name, and only that server's users may use
the bridge. The file holds the bridge's tokens, so it is written whole and kept at 0600.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from typing import Any

import yaml


class BridgeConfigError(Exception):
    """The bridge's config was left as it was; the message says why."""


def merged(theirs: dict[str, Any], ours: dict[str, Any]) -> dict[str, Any]:
    """`theirs` with each of `ours` in its place, map by map."""
    out = dict(theirs)
    for key, value in ours.items():
        # RULE: our settings win over the ones the bridge made
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merged(out[key], value)
        else:
            out[key] = value
    return out


def apply(ours: pathlib.Path, config: pathlib.Path, domain: str) -> None:
    """Merge the settings in `ours` into the bridge's `config`, for the homeserver `domain`."""
    try:
        theirs = yaml.safe_load(config.read_text(encoding="utf-8"))
        wanted = yaml.safe_load(ours.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as broken:
        msg = f"{config} cannot be read ({broken})"
        raise BridgeConfigError(msg) from broken
    if not isinstance(theirs, dict) or not isinstance(wanted, dict):
        msg = f"{config} is not a bridge config"
        raise BridgeConfigError(msg)
    result = merged(theirs, wanted)
    result["homeserver"] = {**result.get("homeserver", {}), "domain": domain}
    # RULE: only matrix users on this laptop may use the bridge
    result["bridge"] = {**result.get("bridge", {}), "permissions": {domain: "user"}}
    _write(config, yaml.safe_dump(result, sort_keys=False, allow_unicode=True))


def _write(config: pathlib.Path, text: str) -> None:
    """Replace `config` whole, so a stop half way leaves the old one."""
    fd, name = tempfile.mkstemp(dir=config.parent, prefix=".config-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
        # mkstemp made it 0600, so the tokens are never readable by others, not even for a moment.
        pathlib.Path(name).replace(config)
    except BaseException:
        pathlib.Path(name).unlink()
        raise
