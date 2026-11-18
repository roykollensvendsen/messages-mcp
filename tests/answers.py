"""Calling a tool end to end, through the MCP server, the service and a made-up inbox."""

import asyncio
import datetime as dt

from messages_mcp.accounts import Accounts
from messages_mcp.device import Chat, Message
from messages_mcp.fake import NOW, FakeInbox
from messages_mcp.server import build_server
from messages_mcp.service import MessagesService


def call(tmp_path, tool, chats=None, *, inbox=None, names=None, simulated=False, **arguments):  # noqa: PLR0913 - one helper for every tool test
    """The structured answer of `tool`, asked with `arguments`, from `chats` or `inbox`."""
    inbox = inbox or (FakeInbox() if chats is None else FakeInbox(chats))
    accounts = Accounts(tmp_path / "accounts.json", names or {})
    service = MessagesService(inbox, accounts, now=lambda: NOW)
    server = build_server(service, simulated=simulated)
    return asyncio.run(server.call_tool(tool, arguments)).structured_content


def chat(name, *bodies, unread=None, receiver="made-up-login-1", sender="Made-up Sender", minutes_ago=5):
    """A made-up chat whose messages are `bodies`, the last `minutes_ago` minutes before NOW."""
    at = NOW - dt.timedelta(minutes=minutes_ago)
    messages = tuple(
        Message(sender, at - dt.timedelta(minutes=len(bodies) - i), body) for i, body in enumerate(bodies, 1)
    )
    return Chat(
        name, receiver, len(bodies) if unread is None else unread, messages[-1].at if messages else None, messages
    )
