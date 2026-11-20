"""sms-bridge's way to the phone: its sms_since tool through a made-up claude-voice bridge."""

from __future__ import annotations

import asyncio
import json

import pytest

from messages_mcp.device import NotSetUpError
from messages_mcp.sms_bridge import RefusedError, SmsBridge
from messages_mcp.sms_hub import BRIDGE_URL, NoKeyYet, PhoneHub, ToolAnswer, refusal, tool_name

KEY = "made-up-plugin-key"
EMPTY = {"messages": [], "cursor": "c-1", "more": False, "dropped": 0, "reset": False}


class FakeBridge:
    """The claude-voice bridge: it notes each call, and gives the answers it holds in turn."""

    def __init__(self, *answers):
        self.answers = list(answers) or [ToolAnswer(is_error=False, structured=EMPTY)]
        self.calls = []

    async def call(self, tool, arguments, key):
        self.calls.append((tool, arguments, key))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class Key:
    def key(self):
        return KEY


def ask(hub, cursor=None):
    return asyncio.run(hub.sms_since(cursor))


def test_the_phone_s_tool_is_called_by_the_bridge_s_name_for_it():
    bridge = FakeBridge()
    assert ask(PhoneHub(bridge, "phone-a", Key())) == EMPTY
    assert bridge.calls == [("phone-a_sms_since", {}, KEY)]


def test_the_cursor_goes_back_exactly_as_it_came():
    bridge = FakeBridge()
    ask(PhoneHub(bridge, "phone-a", Key()), "AZf04TqL7wNc")
    assert bridge.calls[0][1] == {"cursor": "AZf04TqL7wNc"}


def test_the_phone_is_never_asked_without_sms_bridge_s_own_key():
    bridge = FakeBridge()
    with pytest.raises(NotSetUpError):
        ask(PhoneHub(bridge, "phone-a", NoKeyYet()))
    assert bridge.calls == []


def test_with_no_key_yet_the_refusal_says_what_waits_and_what_is_never_used():
    with pytest.raises(NotSetUpError) as refused:
        NoKeyYet().key()
    said = str(refused.value)
    assert "plugin-callback-v1" in said
    assert "login secret and OAuth are never used" in said


def test_a_refusal_from_the_phone_keeps_its_own_words():
    error = json.dumps({"code": "permission_missing", "error": "Grant Read SMS in the made-up app's settings."})
    bridge = FakeBridge(ToolAnswer(is_error=True, structured=None, text=error))
    with pytest.raises(RefusedError) as refused:
        ask(PhoneHub(bridge, "phone-a", Key()))
    assert str(refused.value) == "permission_missing: Grant Read SMS in the made-up app's settings."


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("not json", "the phone refused, with no reason sms-bridge could read"),
        ("[]", "the phone refused, with no reason sms-bridge could read"),
        (json.dumps({"code": 7, "error": "made-up words"}), "made-up words"),
        (json.dumps({"code": "", "error": "made-up words"}), "made-up words"),
        (json.dumps({"code": "made_up"}), "the phone refused, with no reason sms-bridge could read"),
    ],
)
def test_a_refusal_of_another_shape_is_still_said(text, said):
    assert refusal(text) == said


def test_a_long_refusal_is_cut():
    assert len(refusal(json.dumps({"code": "x", "error": "y" * 1000}))) == 300


@pytest.mark.parametrize("phone", ["", "Phone A", "phone_a", "-phone", "a" * 64, "phone/../x"])
def test_a_phone_name_of_another_shape_is_not_set_up(phone):
    with pytest.raises(NotSetUpError, match=r"\[sms\] phone"):
        tool_name(phone)


def test_the_bridge_is_reached_on_this_laptop_only():
    assert BRIDGE_URL.startswith("http://127.0.0.1:")


def test_the_core_polls_the_phone_through_the_hub(tmp_path):
    """The core and the hub together: the cursor of one answer goes back in the next call."""

    class Rooms:
        async def post(self, _sms):
            pass

        async def notice(self, _words):
            pass

    bridge = FakeBridge(ToolAnswer(is_error=False, structured={**EMPTY, "cursor": "c-9"}))
    core = SmsBridge(PhoneHub(bridge, "phone-a", Key()), Rooms(), tmp_path / "position.json")
    asyncio.run(core.poll())
    asyncio.run(core.poll())
    assert [arguments for _, arguments, _ in bridge.calls] == [{}, {"cursor": "c-9"}]
