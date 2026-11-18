"""The owner's list of the devices matrix-door lets in, from `door.toml`.

The owner writes it, and nothing here ever does. Each device is a table of its own, named by
its Tailscale StableID, which stays the same when the device logs in again:

    [[device]]
    name = "phone"
    id = "nFAKE0PHONE0CNTRL"

A missing or empty file lets nobody in. A file that cannot be read, that others can read or
change, that belongs to someone else, or that is a link, is refused. So a broken or planted list
never passes as an open one.
"""

from __future__ import annotations

import os
import stat
import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib

#: The list's name in the plugin's config folder.
FILE = "door.toml"


class DoorListError(Exception):
    """The list cannot be used; the message names the fix."""


def allowed(path: pathlib.Path) -> frozenset[str]:
    """The StableIDs on the owner's list at `path`; none when there is no list."""
    try:
        found = path.lstat()
    except FileNotFoundError:
        return frozenset()
    except OSError as broken:
        msg = f"{path} cannot be read as a list of devices ({broken}); fix it, or remove it to let nobody in"
        raise DoorListError(msg) from broken
    _owned_alone(path, found)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as broken:
        msg = f"{path} cannot be read as a list of devices ({broken}); fix it, or remove it to let nobody in"
        raise DoorListError(msg) from broken
    devices = data.get("device", [])
    if not isinstance(devices, list) or not all(isinstance(d, dict) and isinstance(d.get("id"), str) for d in devices):
        msg = f"{path} must hold [[device]] tables, each with an id; see docs/setup.md"
        raise DoorListError(msg)
    return frozenset(d["id"] for d in devices if d["id"])


def _owned_alone(path: pathlib.Path, found: os.stat_result) -> None:
    """Refuse a list that is a link, belongs to someone else, or that others can read or change."""
    # RULE: a device list that is a link is refused
    if stat.S_ISLNK(found.st_mode):
        msg = f"{path} is a link; make it a file of its own"
        raise DoorListError(msg)
    # RULE: a device list that belongs to someone else is refused
    if found.st_uid != os.getuid():
        msg = f"{path} belongs to someone else; the owner must write it"
        raise DoorListError(msg)
    # RULE: a device list others can read or change is refused
    if found.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        msg = f"{path} can be read or changed by others; run chmod 600 on it"
        raise DoorListError(msg)
