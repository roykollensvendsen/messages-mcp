"""messages_chats, end to end: the chats active this week, newest first, with no message text."""

from answers import call, chat

DAY = 60 * 24


def test_the_chats_active_this_week_come_newest_first_with_no_text(tmp_path):
    chats = [
        chat("Made-up Old", "Made-up secret", minutes_ago=8 * DAY),
        chat("Made-up Tuesday", "Made-up secret", minutes_ago=2 * DAY, unread=0),
        chat("Made-up Today", "Made-up secret", minutes_ago=10),
    ]
    answer = call(tmp_path, "messages_chats", chats)
    assert [(i["chat"], i["unread"]) for i in answer["chats"]] == [("Made-up Today", 1), ("Made-up Tuesday", 0)]
    assert all(i["untrusted"] is True for i in answer["chats"])
    assert "secret" not in str(answer)
    assert answer["say"] == "2 chats this week: “Made-up Today”, “Made-up Tuesday”."


def test_a_quiet_week_is_said(tmp_path):
    answer = call(tmp_path, "messages_chats", [chat("Made-up Old", "Made-up", minutes_ago=8 * DAY)])
    assert (answer["say"], answer["chats"]) == ("No chats this week.", [])


def test_a_chat_with_no_messages_is_not_active(tmp_path):
    assert call(tmp_path, "messages_chats", [chat("Made-up Empty")])["chats"] == []


def test_a_failed_inbox_is_said(tmp_path):
    from messages_mcp.fake import FakeInbox

    inbox = FakeInbox()
    inbox.unreachable = True
    assert call(tmp_path, "messages_chats", inbox=inbox)["outcome"] == "unreachable"
