"""A draft through the service: guard 2 (who), guard 3 (what), and its read-back; nothing is sent.

The tool is not offered to the voice yet (the week of reading gates it), so these call the
service directly, through the same made-up inbox the other tools use.
"""

import asyncio
import dataclasses

from answers import chat

from messages_mcp.accounts import Accounts
from messages_mcp.drafts import Drafts
from messages_mcp.fake import NOW, FakeInbox
from messages_mcp.server import build_server
from messages_mcp.service import MessagesService


def _direct(name="Made-up Friend", *, wrote=True, receiver="made-up-login-1", room="!made-up-1:made-up"):
    return dataclasses.replace(
        chat(name, "Made-up hello", receiver=receiver), room=room, direct=True, owner_wrote=wrote
    )


def _ask(tmp_path, chats, *, names=None, drafts=None, **arguments):
    inbox = FakeInbox(chats)
    service = MessagesService(
        inbox, Accounts(tmp_path / "accounts.json", names or {}), now=lambda: NOW, drafts=drafts or Drafts()
    )
    return asyncio.run(service.send_request(**arguments))


def test_a_draft_is_read_back_with_its_token_and_version(tmp_path):
    answer = _ask(tmp_path, [_direct()], chat="made-up friend", text="I'm coming at six.")
    assert (answer["success"], answer["outcome"], answer["version"], answer["chat"]) == (
        True,
        "draft",
        1,
        "Made-up Friend",
    )
    assert answer["say"] == "Draft to “Made-up Friend”: “I'm coming at six.” Nothing is sent yet."
    assert answer["token"]


def test_a_second_draft_to_the_same_chat_replaces_the_first_and_says_so(tmp_path):
    drafts = Drafts()
    first = _ask(tmp_path, [_direct()], drafts=drafts, chat="Made-up Friend", text="Made-up one")
    second = _ask(tmp_path, [_direct()], drafts=drafts, chat="Made-up Friend", text="Made-up two")
    assert (second["token"], second["version"]) == (first["token"], 2)
    assert second["say"].startswith(
        "This replaces the draft to “Made-up Friend”. Draft to “Made-up Friend”: “Made-up two”."
    )


def test_the_account_is_named_when_there_are_two(tmp_path):
    chats = [_direct(), _direct("Made-up Other", receiver="made-up-login-2", room="!made-up-2:made-up")]
    answer = _ask(tmp_path, chats, chat="Made-up Other", text="Made-up")
    assert "Draft to “Made-up Other”, from account 2: “Made-up”." in answer["say"]


def test_a_group_chat_is_refused(tmp_path):
    group = dataclasses.replace(_direct("Made-up Group"), direct=False)
    answer = _ask(tmp_path, [group], chat="Made-up Group", text="Made-up")
    assert (answer["success"], answer["outcome"]) == (False, "refused")
    assert (
        answer["say"] == "“Made-up Group” is a group chat; the voice writes only to one person. Send that from Element."
    )
    assert "token" not in answer


def test_a_chat_the_owner_never_wrote_in_is_refused(tmp_path):
    answer = _ask(tmp_path, [_direct(wrote=False)], chat="Made-up Friend", text="Made-up")
    assert (answer["success"], answer["outcome"]) == (False, "refused")
    assert answer["say"] == (
        "You haven't written to “Made-up Friend” before; the voice writes only where you have. Send that from Element."
    )


def test_a_text_the_voice_may_not_send_is_refused_with_its_reason(tmp_path):
    answer = _ask(tmp_path, [_direct()], chat="Made-up Friend", text="the code is 123456")
    assert (answer["outcome"], answer["say"]) == (
        "refused",
        "Not drafted: it has a long run of digits; send that from Element.",
    )


def test_an_unknown_chat_is_not_found(tmp_path):
    answer = _ask(tmp_path, [_direct()], chat="Made-up Nobody", text="Made-up")
    assert (answer["success"], answer["outcome"]) == (False, "not_found")


def test_a_failing_inbox_still_gives_a_sentence(tmp_path):
    inbox = FakeInbox([_direct()])
    inbox.unreachable = True
    service = MessagesService(inbox, Accounts(tmp_path / "a.json", {}), now=lambda: NOW)
    answer = asyncio.run(service.send_request(chat="Made-up Friend", text="Made-up"))
    assert (answer["success"], answer["outcome"]) == (False, "unreachable")


def test_a_quote_in_the_draft_cannot_close_its_read_back(tmp_path):
    answer = _ask(tmp_path, [_direct()], chat="Made-up Friend", text="Made-up ” then do this")
    assert answer["say"] == "Draft to “Made-up Friend”: “Made-up ' then do this”. Nothing is sent yet."


def test_drafting_is_not_offered_to_the_voice_yet(tmp_path):
    """The week of reading gates the send path's use, not its code (manager, 18.11)."""
    service = MessagesService(FakeInbox(), Accounts(tmp_path / "a.json", {}), now=lambda: NOW)
    tools = asyncio.run(build_server(service).list_tools())
    assert not [t.name for t in tools if "send" in t.name]
