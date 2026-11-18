"""Command line for messages-mcp: serve the tools, say the gizmo's status once, or run matrix-door."""

from __future__ import annotations

import argparse
import asyncio
import functools
import ssl
import sys

from messages_mcp import door, door_list, platform, settings
from messages_mcp.device import Unconnected
from messages_mcp.fake import FakeGizmo
from messages_mcp.localapi import LocalAPI
from messages_mcp.service import MessagesService

#: Every verb this command has.
COMMANDS: tuple[str, ...] = ("serve", "status", "door")
#: Help is laid out at one width on every terminal, so a page's copy of it stays true.
HELP = functools.partial(argparse.HelpFormatter, width=100)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="messages-mcp", description=__doc__, formatter_class=HELP)
    verbs = parser.add_subparsers(dest="command", required=True)
    serve = verbs.add_parser("serve", formatter_class=HELP, help="serve the tools over stdio, for a voice session")
    status = verbs.add_parser("status", formatter_class=HELP, help="say the gizmo's status, as the voice hears it")
    for verb in (serve, status):
        verb.add_argument("--simulate", action="store_true", help="answer from the made-up gizmo")
    verbs.add_parser("door", formatter_class=HELP, help="let only the listed devices reach the inbox (matrix-door)")
    args = parser.parse_args(argv)
    try:
        configured = settings.load()
    except settings.SettingsError as broken:
        sys.stderr.write(f"{broken}\n")
        return 2
    if args.command == "door":
        return run_door(configured)
    return _answer(args, configured)


def _answer(args: argparse.Namespace, configured: settings.Settings) -> int:
    """Serve the tools, or say the gizmo's status once, as the voice would hear it."""
    simulate = args.simulate or configured.simulate
    service = MessagesService(FakeGizmo() if simulate else Unconnected())

    # The MCP server is a slow import, and only a working command needs it.
    from mcp.types import CallToolResult  # noqa: PLC0415

    from messages_mcp.server import build_server  # noqa: PLC0415

    server = build_server(service, simulated=simulate)
    if args.command == "status":
        # Through the tool itself, so this says exactly what the voice would hear.
        result = asyncio.run(server.call_tool("messages_status", {}))
        answer = result.structured_content if isinstance(result, CallToolResult) else None
        sys.stdout.write(str((answer or {}).get("say", "")) + "\n")
        return 0
    server.run("stdio")  # pragma: no cover - runs until stdin closes
    return 0  # pragma: no cover


def run_door(configured: settings.Settings) -> int:
    """Run matrix-door until stopped; 1, with the reason, when it cannot start."""
    here = platform.current()
    folder = here.config_home() / settings.CONFIG_DIR
    log = door.RefusalLog(here.state_home() / settings.CONFIG_DIR / "door.log")
    try:
        tls = door.tls_context(*configured.door_files())
    except (OSError, ssl.SSLError) as missing:
        msg = f"matrix-door has no usable certificate ({missing}); the owner gets one in docs/setup.md, step C"
        sys.stderr.write(msg + "\n")
        return 1
    api = LocalAPI(here.tailscaled_socket(), door.WHOIS_TIMEOUT)
    try:
        asyncio.run(door.start(api, lambda: door_list.allowed(folder / door_list.FILE), log, tls))
    except (door.DoorError, OSError) as stopped:
        sys.stderr.write(f"{stopped}\n")
        return 1
    except KeyboardInterrupt:  # pragma: no cover - a stop from the terminal
        return 0
    return 0  # pragma: no cover - serve runs until stopped


if __name__ == "__main__":
    sys.exit(main())
