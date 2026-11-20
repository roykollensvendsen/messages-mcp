"""sms-bridge's way to the phone: its `sms_since` tool, called through the claude-voice bridge.

The bridge on this laptop serves the phone's tools under the phone's name, `<phone>_sms_since`,
at `http://127.0.0.1:8811/mcp` (voice-architect, 20.11). A refusal comes back as a tool error
whose text is the protocol's `error.json`: a `code`, such as `permission_missing`, and an
`error` in the phone's own words.

The bridge needs a key that lets sms-bridge call that one tool and nothing else. There is none
yet: the per-plugin key (plugin-callback-v1) waits for the owner's yes. The bridge's login secret
and its OAuth both open the whole bridge, so neither is ever used here. Until the key exists,
`NoKeyYet` refuses, and sms-bridge stops at its start, saying why.

It knows the call itself only through `Caller`, so the tests run on a fake bridge.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from messages_mcp.device import NotSetUpError
from messages_mcp.sms_bridge import RefusedError

#: Where the claude-voice bridge serves its tools on this laptop.
BRIDGE_URL = "http://127.0.0.1:8811/mcp"
#: The shape of a phone's name in the bridge's tool names, such as `phone-a`.
PHONE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
#: The longest refusal kept from the phone: enough for where to grant a permission again.
LONGEST_REFUSAL = 300


@dataclass(frozen=True)
class ToolAnswer:
    """What one tool call gave: whether it is an error, its structured content, and its text."""

    is_error: bool
    structured: Any
    text: str = ""


class Caller(Protocol):
    """One call of a tool on the claude-voice bridge, with a key."""

    async def call(self, tool: str, arguments: dict[str, Any], key: str) -> ToolAnswer:
        """Call `tool` with `arguments`, showing `key`."""


class KeySource(Protocol):
    """Where sms-bridge's key to the bridge comes from."""

    def key(self) -> str:
        """The key, or `NotSetUpError` saying why there is none."""


class NoKeyYet:
    """The key source until plugin-callback-v1 is approved: it always refuses."""

    def key(self) -> str:
        """Refuse, saying what is missing and what is never used instead."""
        msg = (
            "sms-bridge has no key to the claude-voice bridge yet: the per-plugin key (plugin-callback-v1)"
            " waits for the owner's yes. The bridge's login secret and OAuth are never used, since"
            " each opens the whole bridge."
        )
        raise NotSetUpError(msg)


def tool_name(phone: str) -> str:
    """The bridge's name for `phone`'s sms_since tool; `NotSetUpError` for a name of another shape."""
    if not PHONE_NAME.fullmatch(phone):
        msg = "no phone is named for sms-bridge; set [sms] phone in the config, as the bridge names it"
        raise NotSetUpError(msg)
    return f"{phone}_sms_since"


class PhoneHub:
    """The phone's `sms_since`, through `caller`, as sms-bridge's core asks for it."""

    def __init__(self, caller: Caller, phone: str, keys: KeySource) -> None:
        """Call `phone`'s tool through `caller`, with the key `keys` gives."""
        self.caller = caller
        self.tool = tool_name(phone)
        self.keys = keys

    async def sms_since(self, cursor: str | None) -> Any:  # noqa: ANN401 - sms_bridge.parse checks it
        """The phone's answer for `cursor`; `RefusedError`, in the phone's words, when it refuses."""
        # RULE: the phone is never asked without sms-bridge's own key
        key = self.keys.key()
        # RULE: the cursor goes back exactly as it came
        arguments = {} if cursor is None else {"cursor": cursor}
        answer = await self.caller.call(self.tool, arguments, key)
        if answer.is_error:
            raise RefusedError(refusal(answer.text))
        return answer.structured


def refusal(text: str) -> str:
    """The phone's refusal as `code: words`, from the protocol's error.json, kept short."""
    try:
        given = json.loads(text)
    except ValueError:
        given = None
    if isinstance(given, dict) and isinstance(given.get("error"), str):
        code = given.get("code")
        said = f"{code}: {given['error']}" if isinstance(code, str) and code else given["error"]
    else:
        said = "the phone refused, with no reason sms-bridge could read"
    return said[:LONGEST_REFUSAL]
