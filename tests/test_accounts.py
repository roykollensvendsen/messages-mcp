"""Account labels: none while there is one login, numbers that never shift, and the owner's own names."""

import json

from messages_mcp.accounts import Accounts

ONE, TWO = "made-up-login-1", "made-up-login-2"


def _accounts(tmp_path, names=None):
    return Accounts(tmp_path / "state" / "accounts.json", names or {})


def test_one_login_means_no_label(tmp_path):
    labels, news = _accounts(tmp_path).labels({ONE})
    assert labels == {ONE: None}
    assert news is None


def test_a_second_login_is_numbered_and_announced_once(tmp_path):
    accounts = _accounts(tmp_path)
    accounts.labels({ONE})
    labels, news = accounts.labels({ONE, TWO})
    assert labels == {ONE: "account 1", TWO: "account 2"}
    assert news == "A second Messenger account is now bridged; it's called account 2."
    assert accounts.labels({ONE, TWO})[1] is None


def test_a_login_keeps_the_number_it_got_the_first_time_it_was_seen(tmp_path):
    """Even when the earliest login's chats are gone, as voice-architect asked."""
    accounts = _accounts(tmp_path)
    accounts.labels({ONE})
    accounts.labels({ONE, TWO})
    again = _accounts(tmp_path)
    assert again.labels({TWO})[0] == {TWO: "account 2"}
    assert again.labels({ONE, TWO})[0] == {ONE: "account 1", TWO: "account 2"}


def test_the_owner_may_name_a_login(tmp_path):
    accounts = _accounts(tmp_path, {TWO: "work"})
    accounts.labels({ONE})
    labels, news = accounts.labels({ONE, TWO})
    assert labels == {ONE: "account 1", TWO: "work"}
    assert news == "A second Messenger account is now bridged; it's called work."


def test_a_chat_with_no_login_is_account_unknown(tmp_path):
    labels, _ = _accounts(tmp_path).labels({ONE, ""})
    assert labels[""] == "account ?"


def test_the_numbers_are_kept_where_only_the_owner_reads_them(tmp_path):
    accounts = _accounts(tmp_path)
    accounts.labels({ONE, TWO})
    assert accounts.path.stat().st_mode & 0o777 == 0o600
    assert accounts.path.parent.stat().st_mode & 0o777 == 0o700
    assert json.loads(accounts.path.read_text())["numbers"] == {ONE: 1, TWO: 2}


def test_a_broken_store_starts_again_rather_than_failing(tmp_path):
    path = tmp_path / "state" / "accounts.json"
    path.parent.mkdir(parents=True)
    path.write_text("not json")
    assert Accounts(path, {}).labels({ONE})[0] == {ONE: None}
