"""Renewing matrix-door's certificate, which `tailscale cert` makes and never renews by itself.

Tailscale's certificates last 90 days, and Tailscale leaves the files it writes for the owner to
renew. matrix-door-cert.timer runs this each week: it asks for a certificate with at least a
month left, for the laptop's own tailnet name, and keeps the key readable by the owner only.
"""

from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING, Protocol

from messages_mcp.localapi import LocalAPIError

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Callable

#: The least validity the renewed certificate has, in Go's duration form.
MIN_VALIDITY = "720h"


class CertError(Exception):
    """The certificate could not be renewed; the message says why."""


class NameSource(Protocol):
    """Anything that knows the laptop's own tailnet name."""

    async def own_name(self) -> str:
        """The name, as its certificate names it."""
        ...


def _run(command: list[str]) -> int:  # pragma: no cover - runs the real tailscale
    return subprocess.run(command, check=False).returncode  # noqa: S603


def renew(api: NameSource, cert: pathlib.Path, key: pathlib.Path, run: Callable[[list[str]], int] = _run) -> None:
    """Ask tailscale for a certificate with a month left at least, written to `cert` and `key`."""
    try:
        # RULE: the certificate is renewed for this laptop by its tailnet name
        name = asyncio.run(api.own_name())
    except LocalAPIError as broken:
        raise CertError(str(broken)) from broken
    command = ["tailscale", "cert", "--min-validity", MIN_VALIDITY, "--cert-file", str(cert), "--key-file", str(key)]
    if run([*command, name]) != 0:
        msg = f"tailscale cert failed for {name}; see docs/setup.md, step C"
        raise CertError(msg)
    # RULE: the door key is readable by the owner only
    key.chmod(0o600)
