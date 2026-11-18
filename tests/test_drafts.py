"""Drafts: one per recipient, a new version on each change, gone 15 minutes after the last one; and guard 3."""

import pytest

from messages_mcp.drafts import EXPIRY, Drafts, refused_text


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _drafts():
    clock = _Clock()
    return Drafts(clock=clock), clock


def test_a_draft_gets_a_token_and_version_one():
    drafts, _ = _drafts()
    draft, replaced = drafts.write("made-up-login-1", "!room:made-up", "Made-up Friend", "Made-up text")
    assert (draft.version, draft.text, replaced) == (1, "Made-up text", False)
    assert len(draft.token) >= 16


def test_each_change_to_a_draft_gives_a_new_version():
    drafts, _ = _drafts()
    first, _ = drafts.write("made-up-login-1", "!room:made-up", "Made-up Friend", "Made-up one")
    second, replaced = drafts.write("made-up-login-1", "!room:made-up", "Made-up Friend", "Made-up two")
    assert (second.token, second.version, second.text, replaced) == (first.token, 2, "Made-up two", True)


def test_there_is_one_draft_per_recipient():
    drafts, _ = _drafts()
    one, _ = drafts.write("made-up-login-1", "!a:made-up", "Made-up A", "Made-up")
    other, replaced = drafts.write("made-up-login-1", "!b:made-up", "Made-up B", "Made-up")
    assert other.token != one.token
    assert replaced is False
    elsewhere, _ = drafts.write("made-up-login-2", "!a:made-up", "Made-up A", "Made-up")
    assert elsewhere.token != one.token


def test_a_stale_version_is_refused():
    drafts, _ = _drafts()
    first, _ = drafts.write("made-up-login-1", "!room:made-up", "Made-up Friend", "Made-up one")
    drafts.write("made-up-login-1", "!room:made-up", "Made-up Friend", "Made-up two")
    found = drafts.check(first.token, 1)
    assert found == "it changed; read it back first"
    assert drafts.check(first.token, 2).text == "Made-up two"


def test_a_draft_expires_fifteen_minutes_after_its_last_change():
    drafts, clock = _drafts()
    draft, _ = drafts.write("made-up-login-1", "!room:made-up", "Made-up Friend", "Made-up one")
    clock.now += EXPIRY - 1
    drafts.write("made-up-login-1", "!room:made-up", "Made-up Friend", "Made-up two")
    clock.now += EXPIRY - 1
    assert drafts.check(draft.token, 2).version == 2
    clock.now += 2
    assert drafts.check(draft.token, 2) == "that draft expired"
    renewed, replaced = drafts.write("made-up-login-1", "!room:made-up", "Made-up Friend", "Made-up three")
    assert (renewed.version, replaced) == (1, False)
    assert renewed.token != draft.token


def test_an_unknown_token_is_no_draft():
    drafts, _ = _drafts()
    assert drafts.check("made-up-token", 1) == "there is no such draft"


def test_drafts_live_only_in_memory():
    """messages-v1 §8: a draft is held by the final sender only, and a restart drops it."""
    drafts, _ = _drafts()
    draft, _ = drafts.write("made-up-login-1", "!room:made-up", "Made-up Friend", "Made-up")
    assert Drafts(clock=lambda: 1000.0).check(draft.token, 1) == "there is no such draft"


@pytest.mark.parametrize(
    "text",
    [
        "see https://made-up.example.invalid/x",
        "see http://made-up.example.invalid",
        "go to www.made-up.example.invalid",
        "made-up.example.invalid/path",
        "mail made-up@example.invalid",
    ],
)
def test_a_text_with_a_link_is_refused(text):
    assert refused_text(text) == "it has a link; send that from Element"


@pytest.mark.parametrize("text", ["the code is 123456", "call 12 34 56 78", "it's 4-8-15-16-23-42", "1.2.3.4.5.6"])
def test_a_text_with_a_long_run_of_digits_is_refused(text):
    assert refused_text(text) == "it has a long run of digits; send that from Element"


def test_a_text_over_a_thousand_characters_is_refused():
    assert refused_text("a" * 1000) is None
    assert refused_text("a" * 1001) == "it is over 1000 characters; send that from Element"


@pytest.mark.parametrize("text", ["I'm coming at six.", "See you at 18.30 on the 5th.", "Room 12345 is fine."])
def test_ordinary_words_pass(text):
    assert refused_text(text) is None


def test_an_empty_text_is_refused():
    assert refused_text("   ") == "there is nothing to send"


def test_writing_to_an_expired_draft_starts_a_new_one():
    drafts, clock = _drafts()
    old, _ = drafts.write("made-up-login-1", "!room:made-up", "Made-up Friend", "Made-up one")
    clock.now += EXPIRY + 1
    new, replaced = drafts.write("made-up-login-1", "!room:made-up", "Made-up Friend", "Made-up two")
    assert (new.version, replaced) == (1, False)
    assert drafts.check(old.token, 1) == "there is no such draft"
