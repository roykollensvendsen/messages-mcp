"""messages_read, end to end: one chat's unread messages, at most ten, each cut, none marked read."""

from answers import call, chat

from messages_mcp.service import READ_CUT, READ_MOST


def test_a_read_gives_at_most_ten_of_the_unread_messages(tmp_path):
    bodies = [f"Made-up {n}" for n in range(15)]
    answer = call(tmp_path, "messages_read", [chat("Made-up Group", *bodies, unread=12)], chat="Made-up Group")
    assert [m["quoted_text"] for m in answer["messages"]] == bodies[-READ_MOST:]
    assert all(m["untrusted"] is True for m in answer["messages"])
    assert answer["say"].startswith("Made-up Group, 10 unread. Made-up Sender wrote: “Made-up 5”.")
    assert answer["say"].endswith("They stay unread.")


def test_only_the_unread_ones_are_read(tmp_path):
    answer = call(
        tmp_path,
        "messages_read",
        [chat("Made-up Group", "Made-up a", "Made-up b", "Made-up c", unread=1)],
        chat="made-up group",
    )
    assert [m["quoted_text"] for m in answer["messages"]] == ["Made-up c"]


def test_each_message_is_cut_to_three_hundred(tmp_path):
    answer = call(tmp_path, "messages_read", [chat("Made-up Friend", "made-up " * 80)], chat="Made-up Friend")
    [message] = answer["messages"]
    assert message["cut"] is True
    assert len(message["quoted_text"]) <= READ_CUT + 1


def test_a_chat_with_nothing_unread_says_so(tmp_path):
    answer = call(tmp_path, "messages_read", [chat("Made-up Friend", "Made-up", unread=0)], chat="Made-up Friend")
    assert (answer["success"], answer["say"], answer["messages"]) == (True, "No unread messages in Made-up Friend.", [])


def test_a_chat_can_be_named_by_part_of_its_name(tmp_path):
    answer = call(tmp_path, "messages_read", [chat("Made-up Football Group", "Made-up")], chat="football")
    assert answer["chat"] == "Made-up Football Group"


def test_an_unknown_chat_is_said(tmp_path):
    answer = call(tmp_path, "messages_read", [chat("Made-up Friend", "Made-up")], chat="nobody")
    assert (answer["success"], answer["outcome"], answer["say"]) == (False, "not_found", "No chat by that name.")


def test_a_name_that_fits_several_chats_asks_which(tmp_path):
    chats = [chat("Made-up Group A", "Made-up"), chat("Made-up Group B", "Made-up")]
    answer = call(tmp_path, "messages_read", chats, chat="group")
    assert answer["say"] == "More than one chat matches: Made-up Group A, Made-up Group B. Which one?"


def test_an_exact_name_wins_over_a_longer_one(tmp_path):
    chats = [chat("Made-up Group", "Made-up"), chat("Made-up Group B", "Made-up")]
    assert call(tmp_path, "messages_read", chats, chat="made-up group")["chat"] == "Made-up Group"


def test_a_failed_inbox_is_said(tmp_path):
    from messages_mcp.fake import FakeInbox

    inbox = FakeInbox()
    inbox.unreachable = True
    assert call(tmp_path, "messages_read", inbox=inbox, chat="Made-up Friend")["outcome"] == "unreachable"
