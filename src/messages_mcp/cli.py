"""Command line for messages-mcp: serve the tools, say the status once, run matrix-door, or set it up."""

from __future__ import annotations

import argparse
import asyncio
import functools
import pathlib
import ssl
import sys

from messages_mcp import bridge_config, door, door_cert, door_list, ntfy_config, platform, settings
from messages_mcp.device import Unconnected
from messages_mcp.fake import FakeGizmo
from messages_mcp.localapi import LocalAPI, LocalAPIError
from messages_mcp.service import MessagesService

#: Every verb this command has.
COMMANDS: tuple[str, ...] = ("serve", "status", "door", "door-cert", "bridge-config", "ntfy-config")
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
    verbs.add_parser("door-cert", formatter_class=HELP, help="renew matrix-door's certificate, a month ahead")
    bridge = verbs.add_parser("bridge-config", formatter_class=HELP, help="put our settings into mautrix-meta's config")
    bridge.add_argument("ours", type=pathlib.Path, help="our settings: config/mautrix-meta/messages-mcp.yaml")
    bridge.add_argument("config", type=pathlib.Path, help="the config the bridge generated for itself")
    ntfy = verbs.add_parser("ntfy-config", formatter_class=HELP, help="write ntfy's config for this laptop")
    ntfy.add_argument("ours", type=pathlib.Path, help="our settings: config/ntfy/server.yml")
    ntfy.add_argument("config", type=pathlib.Path, help="where ntfy reads its config: ~/.config/ntfy/server.yml")
    args = parser.parse_args(argv)
    try:
        configured = settings.load()
    except settings.SettingsError as broken:
        sys.stderr.write(f"{broken}\n")
        return 2
    runs = {
        "door": run_door,
        "door-cert": run_door_cert,
        "bridge-config": run_bridge_config,
        "ntfy-config": run_ntfy_config,
    }
    return runs.get(args.command, _answer)(args, configured)


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


def run_door(_args: argparse.Namespace, configured: settings.Settings) -> int:
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
    finally:
        log.flush()
    return 0  # pragma: no cover - serve runs until stopped


def run_door_cert(_args: argparse.Namespace, configured: settings.Settings) -> int:
    """Renew the door's certificate; 1, with the reason, when it cannot."""
    api = LocalAPI(platform.current().tailscaled_socket(), door.WHOIS_TIMEOUT)
    try:
        door_cert.renew(api, *configured.door_files())
    except door_cert.CertError as failed:
        sys.stderr.write(f"the door's certificate was not renewed: {failed}\n")
        return 1
    return 0  # pragma: no cover - only with a real tailscaled


def run_bridge_config(args: argparse.Namespace, _configured: settings.Settings) -> int:
    """Merge our settings into the bridge's config, for this laptop's name; 1 when it cannot."""
    api = LocalAPI(platform.current().tailscaled_socket(), door.WHOIS_TIMEOUT)
    try:
        bridge_config.apply(args.ours, args.config, asyncio.run(api.own_name()))
    except (LocalAPIError, bridge_config.BridgeConfigError) as failed:
        sys.stderr.write(f"{failed}; {args.config} left as it was\n")
        return 1
    return 0


def run_ntfy_config(args: argparse.Namespace, _configured: settings.Settings) -> int:
    """Write ntfy's config for this laptop's name; 1 when it cannot."""
    here = platform.current()
    api = LocalAPI(here.tailscaled_socket(), door.WHOIS_TIMEOUT)
    try:
        ntfy_config.apply(args.ours, args.config, asyncio.run(api.own_name()), here.state_home())
    except (LocalAPIError, ntfy_config.NtfyConfigError) as failed:
        sys.stderr.write(f"{failed}; {args.config} not written\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
