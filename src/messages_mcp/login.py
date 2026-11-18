"""The plugin's own Matrix login: a device of its own, on the owner's Matrix user.

The owner types the password once, at a prompt that does not echo it; it is never kept.
What is kept is the access token, in the config folder, readable by the owner only. That
token could send as the owner, so the reader has no way to send: it syncs and nothing else
(`matrix.py`). Sending comes in v1 after the reading part, in its own slice, with the owner's
spoken "send", an undo delay and limits.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from messages_mcp.bridge_config import write_private
from messages_mcp.matrix import on_this_laptop

if TYPE_CHECKING:
    import pathlib

#: The name the owner sees for this device in Element's session list.
DEVICE_NAME = "messages-mcp (voice)"


class LoginError(Exception):
    """The login failed; the message says why, with no password in it."""


async def log_in(homeserver: str, user: str, password: str, path: pathlib.Path) -> str:
    """Log in as `user` and keep the token in `path`; the device ID made."""
    # mautrix and aiohttp are slow imports, and only this verb needs them here.
    from aiohttp import ClientSession  # noqa: PLC0415
    from mautrix.client import ClientAPI  # noqa: PLC0415
    from mautrix.errors import MatrixRequestError  # noqa: PLC0415
    from mautrix.types import UserID  # noqa: PLC0415

    if not on_this_laptop(homeserver):
        msg = f"{homeserver} is not on this laptop; the plugin logs in only to the Synapse here"
        raise LoginError(msg)
    async with ClientSession() as session:
        client = ClientAPI(base_url=homeserver, client_session=session)
        try:
            answer = await client.login(identifier=UserID(user), password=password, device_name=DEVICE_NAME)
        except MatrixRequestError as refused:
            msg = f"Synapse refused the login ({refused})"
            raise LoginError(msg) from refused
        except Exception as failed:
            msg = f"Synapse at {homeserver} did not answer"
            raise LoginError(msg) from failed
    kept = {
        "homeserver": homeserver,
        "user_id": str(answer.user_id),
        "device_id": str(answer.device_id),
        "access_token": answer.access_token,
    }
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # RULE: the plugins login is readable by the owner only
    write_private(path, json.dumps(kept))
    return str(answer.device_id)
