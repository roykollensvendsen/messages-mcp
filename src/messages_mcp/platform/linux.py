"""Linux, by the XDG base directory rules."""

from __future__ import annotations

import os
import pathlib


class Linux:
    """The folders as a Linux desktop or server keeps them."""

    def config_home(self) -> pathlib.Path:
        """`$XDG_CONFIG_HOME`, or `~/.config` when it is unset or empty."""
        return pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config")

    def data_home(self) -> pathlib.Path:
        """`$XDG_DATA_HOME`, or `~/.local/share` when it is unset or empty."""
        return pathlib.Path(os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local/share")

    def state_home(self) -> pathlib.Path:
        """`$XDG_STATE_HOME`, or `~/.local/state` when it is unset or empty."""
        return pathlib.Path(os.environ.get("XDG_STATE_HOME") or pathlib.Path.home() / ".local/state")

    def tailscaled_socket(self) -> pathlib.Path:
        """The socket a Linux tailscaled serves its local API on."""
        return pathlib.Path("/var/run/tailscale/tailscaled.sock")
