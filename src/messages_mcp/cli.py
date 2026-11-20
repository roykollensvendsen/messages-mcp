"""Command line for messages-mcp: serve the tools, say what is unread, run matrix-door or sms-bridge, or set them up."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import getpass
import pathlib
import signal
import ssl
import sys
from typing import TYPE_CHECKING

from messages_mcp import bridge_config, door, door_cert, door_list, login, ntfy_config, platform, settings
from messages_mcp.accounts import Accounts
from messages_mcp.device import NotSetUpError
from messages_mcp.fake import FakeInbox
from messages_mcp.localapi import LocalAPI, LocalAPIError
from messages_mcp.matrix import owner_of
from messages_mcp.service import MessagesService
from messages_mcp.sms_bridge import SmsBridge
from messages_mcp.sms_hub import NoKeyYet, PhoneHub
from messages_mcp.sms_rooms import MatrixRooms, as_token, write_registration

if TYPE_CHECKING:
    from messages_mcp.sms_hub import Caller, KeySource

#: Every verb this command has.
COMMANDS: tuple[str, ...] = (
    "serve",
    "unread",
    "login",
    "door",
    "door-cert",
    "bridge-config",
    "ntfy-config",
    "sms-bridge",
)
#: sms-bridge's folder in the data home (registration, rooms) and in the state home (its place).
SMS_BRIDGE = "sms-bridge"
#: sms-bridge's exit when it is not set up; its unit does not restart on it.
NOT_SET_UP = 2
#: Help is laid out at one width on every terminal, so a page's copy of it stays true.
HELP = functools.partial(argparse.HelpFormatter, width=100)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="messages-mcp", description=__doc__, formatter_class=HELP)
    verbs = parser.add_subparsers(dest="command", required=True)
    serve = verbs.add_parser("serve", formatter_class=HELP, help="serve the tools over stdio, for a voice session")
    unread = verbs.add_parser("unread", formatter_class=HELP, help="say what is unread once, as the voice hears it")
    for verb in (serve, unread):
        verb.add_argument("--simulate", action="store_true", help="answer from the made-up chats")
    verbs.add_parser("login", formatter_class=HELP, help="give the plugin its own read-only login to Synapse")
    verbs.add_parser("door", formatter_class=HELP, help="let only the listed devices reach the inbox (matrix-door)")
    verbs.add_parser("door-cert", formatter_class=HELP, help="renew matrix-door's certificate, a month ahead")
    bridge = verbs.add_parser("bridge-config", formatter_class=HELP, help="put our settings into mautrix-meta's config")
    bridge.add_argument("ours", type=pathlib.Path, help="our settings: config/mautrix-meta/messages-mcp.yaml")
    bridge.add_argument("config", type=pathlib.Path, help="the config the bridge generated for itself")
    ntfy = verbs.add_parser("ntfy-config", formatter_class=HELP, help="write ntfy's config for this laptop")
    ntfy.add_argument("ours", type=pathlib.Path, help="our settings: config/ntfy/server.yml")
    ntfy.add_argument("config", type=pathlib.Path, help="where ntfy reads its config: ~/.config/ntfy/server.yml")
    sms = verbs.add_parser("sms-bridge", formatter_class=HELP, help="pull the phone's texts into Matrix (sms-bridge)")
    sms.add_argument("action", choices=("run", "registration"), help="run it, or write its registration for Synapse")
    args = parser.parse_args(argv)
    try:
        configured = settings.load()
    except settings.SettingsError as broken:
        sys.stderr.write(f"{broken}\n")
        return 2
    runs = {
        "login": run_login,
        "door": run_door,
        "door-cert": run_door_cert,
        "bridge-config": run_bridge_config,
        "ntfy-config": run_ntfy_config,
        "sms-bridge": run_sms_bridge,
    }
    return runs.get(args.command, _answer)(args, configured)


def _answer(args: argparse.Namespace, configured: settings.Settings) -> int:
    """Serve the tools, or say what is unread once, as the voice would hear it."""
    simulate = args.simulate or configured.simulate
    service = _service(configured, simulate=simulate)

    # The MCP server is a slow import, and only a working command needs it.
    from mcp.types import CallToolResult  # noqa: PLC0415

    from messages_mcp.server import build_server  # noqa: PLC0415

    server = build_server(service, simulated=simulate)
    if args.command == "unread":
        # Through the tool itself, so this says exactly what the voice would hear.
        result = asyncio.run(server.call_tool("messages_unread", {}))
        answer = result.structured_content if isinstance(result, CallToolResult) else None
        sys.stdout.write(str((answer or {}).get("say", "")) + "\n")
        return 0
    server.run("stdio")  # pragma: no cover - runs until stdin closes
    return 0  # pragma: no cover


def _service(configured: settings.Settings, *, simulate: bool) -> MessagesService:
    """The core, reading the made-up chats or the real inbox."""
    state = platform.current().state_home() / settings.CONFIG_DIR
    if simulate:
        # The made-up chats number their made-up logins apart from the real ones.
        return MessagesService(FakeInbox(), Accounts(state / "accounts-simulated.json", configured.accounts))
    from messages_mcp.matrix import MatrixInbox  # noqa: PLC0415 - only a real read needs aiohttp

    return MessagesService(MatrixInbox(configured.login_file()), Accounts(state / "accounts.json", configured.accounts))


def run_login(_args: argparse.Namespace, configured: settings.Settings) -> int:
    """Log the plugin in as the owner's Matrix user, on a device of its own; 1 when refused."""
    user = input("Your Matrix user, as @name:<laptop>.<tailnet>.ts.net: ").strip()
    password = getpass.getpass("Its password (not shown, never kept): ")
    path = configured.login_file()
    try:
        device = asyncio.run(login.log_in(configured.homeserver, user, password, path))
    except login.LoginError as refused:
        sys.stderr.write(f"not logged in: {refused}\n")
        return 1
    sys.stdout.write(f"Logged in as device {device}. The login is kept in {path}, readable by you only.\n")
    return 0


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


def run_sms_bridge(
    args: argparse.Namespace,
    configured: settings.Settings,
    keys: KeySource | None = None,
    caller: Caller | None = None,
) -> int:
    """Run sms-bridge until stopped, or write its registration; 2, with the reason, when not set up."""
    here = platform.current()
    data = here.data_home() / SMS_BRIDGE
    if args.action == "registration":
        return _sms_registration(data / "registration.yaml")
    try:
        bridge = sms_bridge_parts(configured, data, here.state_home() / SMS_BRIDGE, keys or NoKeyYet(), caller)
    except NotSetUpError as missing:
        sys.stderr.write(f"sms-bridge is not set up: {missing}\n")
        return NOT_SET_UP
    return _run_until_stopped(bridge)  # pragma: no cover - runs until stopped


def sms_bridge_parts(
    configured: settings.Settings,
    data: pathlib.Path,
    state: pathlib.Path,
    keys: KeySource,
    caller: Caller | None,
) -> SmsBridge:
    """sms-bridge put together; `NotSetUpError` at once, before any poll, for anything missing."""
    # RULE: sms-bridge without its own key stops at its start
    keys.key()
    if caller is None:
        msg = "sms-bridge has no client for the claude-voice bridge yet; it comes with the key (plugin-callback-v1)"
        raise NotSetUpError(msg)
    hub = PhoneHub(caller, configured.sms_phone, keys)
    token = as_token(data / "registration.yaml")
    rooms = MatrixRooms(configured.homeserver, token, owner_of(configured.login_file()), data / "rooms.json")
    # RULE: a broken room list stops sms-bridge at its start
    rooms.check()
    return SmsBridge(hub, rooms, state / "position.json")


def _sms_registration(path: pathlib.Path) -> int:
    """Write sms-bridge's registration for this laptop's Matrix server; 1 when tailscaled cannot name it."""
    api = LocalAPI(platform.current().tailscaled_socket(), door.WHOIS_TIMEOUT)
    try:
        server = asyncio.run(api.own_name())
    except LocalAPIError as failed:
        sys.stderr.write(f"{failed}; no registration written\n")
        return 1
    if write_registration(path, server):
        sys.stdout.write(f"Wrote sms-bridge's registration to {path}, readable by you only.\n")
    else:
        sys.stdout.write(f"{path} is there already, and is kept: Synapse holds its tokens.\n")
    return 0


def _run_until_stopped(bridge: SmsBridge) -> int:  # pragma: no cover - runs until stopped
    """Poll until systemd or the terminal stops it."""

    async def go() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for stopping in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(stopping, stop.set)
        await bridge.run(stop)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(go())
    return 0


if __name__ == "__main__":
    sys.exit(main())
