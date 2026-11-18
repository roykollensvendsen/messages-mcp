"""tailscaled's local API: who a tailnet address belongs to, this laptop's own addresses, and Funnel.

tailscaled answers plain HTTP on a Unix socket that only this machine can reach. Each question
here is one request with a time limit. Every failure, a slow answer included, raises
`LocalAPIError`, so a caller can refuse instead of guess.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pathlib

#: The Host tailscaled expects on its local API.
HOST = "local-tailscaled.sock"
#: An answer bigger than this is not one tailscaled gives.
MOST = 1 << 20


class LocalAPIError(Exception):
    """tailscaled could not answer, answered too slowly, or answered with something unusable."""


class LocalAPI:
    """Questions to tailscaled, each bounded by `timeout` seconds."""

    def __init__(self, socket: pathlib.Path | str, timeout: float) -> None:
        """Ask the tailscaled listening on `socket`."""
        self.socket = str(socket)
        self.timeout = timeout

    async def get(self, path: str) -> Any:  # noqa: ANN401 - the JSON tailscaled sends
        """The JSON tailscaled answers to `GET path`."""
        try:
            return await asyncio.wait_for(self._get(path), self.timeout)
        except TimeoutError as slow:
            msg = f"tailscaled did not answer {path} within {self.timeout:g} s"
            raise LocalAPIError(msg) from slow
        except (OSError, ValueError) as broken:
            msg = f"tailscaled could not answer {path} ({broken})"
            raise LocalAPIError(msg) from broken

    async def _get(self, path: str) -> Any:  # noqa: ANN401
        reader, writer = await asyncio.open_unix_connection(self.socket)
        try:
            writer.write(f"GET {path} HTTP/1.0\r\nHost: {HOST}\r\n\r\n".encode("ascii"))
            await writer.drain()
            answer = await reader.read(MOST)
        finally:
            writer.close()
        head, _, body = answer.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].split()
        if len(status) < 2 or status[1] != b"200":  # noqa: PLR2004 - the status code's place in its line
            msg = f"the answer was {head[:60]!r}"
            raise ValueError(msg)
        return json.loads(body)

    async def whois(self, ip: str, port: int) -> dict[str, Any]:
        """The node record tailscaled keeps for the device at `ip`:`port`."""
        answer = await self.get(f"/localapi/v0/whois?addr={ip}:{port}")
        node = answer.get("Node") if isinstance(answer, dict) else None
        if not isinstance(node, dict) or not node.get("StableID"):
            msg = "whois answered with no node"
            raise LocalAPIError(msg)
        return node

    async def own_addresses(self) -> list[str]:
        """The laptop's own addresses on the tailnet."""
        answer = await self.get("/localapi/v0/status")
        addresses = answer.get("Self", {}).get("TailscaleIPs") if isinstance(answer, dict) else None
        if not addresses:
            msg = "status gave no tailnet address for this laptop"
            raise LocalAPIError(msg)
        return [str(a) for a in addresses]

    async def funnel_ports(self) -> set[int]:
        """The ports Funnel opens to the whole internet."""
        answer = await self.get("/localapi/v0/serve-config")
        allowed = (answer or {}).get("AllowFunnel") or {} if isinstance(answer, dict) else {}
        return {int(where.rsplit(":", 1)[1]) for where, on in allowed.items() if on}
