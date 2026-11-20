"""The copy of the phone's sms_since answer schema stays the file it is pinned to."""

import json
import pathlib

import pytest
import schema_pin
import yaml

ROOT = pathlib.Path(__file__).parent.parent

#: A made-up number in international form, built here so none is written in the repository.
NUMBER = "+" + "47" + "0" * 6 + "21"


def test_the_copy_is_the_file_it_is_pinned_to():
    assert schema_pin.check_copy() == []


def test_the_pin_names_a_whole_commit_and_a_whole_digest():
    pin = schema_pin.pinned()
    assert pin["repo"] == "roykollensvendsen/claude-voice-mobile"
    assert pin["path"] == "docs/plugins/sms/sms-since-answer.json"
    assert len(pin["commit"]) == 40
    assert len(pin["sha256"]) == 64
    assert int(pin["commit"], 16) >= 0
    assert int(pin["sha256"], 16) >= 0


def test_a_number_is_written_as_the_placeholder_before_the_digest():
    assert schema_pin.normalised(f'"from": "{NUMBER}"') == '"from": "+<country><number>"'
    assert schema_pin.digest(f"x {NUMBER} y") == schema_pin.digest("x +<country><number> y")


def test_a_changed_copy_is_named_with_its_new_digest(tmp_path):
    copy = tmp_path / "copy.json"
    copy.write_text(schema_pin.COPY.read_text(encoding="utf-8").replace('"maxItems": 50', '"maxItems": 51'))
    [said] = schema_pin.check_copy(copy)
    assert f"not the file pinned at {schema_pin.pinned()['commit'][:12]}" in said
    assert schema_pin.digest(copy.read_text(encoding="utf-8")) in said


def test_a_copy_holding_a_real_looking_number_is_refused(tmp_path):
    copy = tmp_path / "copy.json"
    copy.write_text(schema_pin.COPY.read_text(encoding="utf-8").replace("+<country><number>", NUMBER))
    said = schema_pin.check_copy(copy)
    assert any("international form" in line for line in said)


#: The phone's own file, holding a real-looking number where the copy has the placeholder.
def theirs():
    return schema_pin.COPY.read_text(encoding="utf-8").replace("+<country><number>", NUMBER)


def by_ref(at_commit, at_main):
    """A fake fetch keyed by ref: the pinned commit gives `at_commit`, main gives `at_main`."""
    commit = schema_pin.pinned()["commit"]
    asked = []

    def get(url):
        asked.append(url)
        if f"/{commit}/" in url:
            return at_commit
        assert "/main/" in url, url
        return at_main

    return get, asked


def test_the_pin_is_proved_against_the_file_at_its_own_commit():
    get, asked = by_ref(theirs(), "{}")
    assert schema_pin.check_pinned(get) == []
    commit = schema_pin.pinned()["commit"]
    assert asked == [
        f"https://raw.githubusercontent.com/roykollensvendsen/claude-voice-mobile/{commit}/docs/plugins/sms/sms-since-answer.json"
    ]


def test_a_pin_whose_commit_holds_another_file_is_wrong():
    get, _ = by_ref("{}", theirs())
    [said] = schema_pin.check_pinned(get)
    assert "the pin is wrong" in said
    assert schema_pin.digest("{}") in said


def test_the_phone_s_main_matching_the_pin_says_nothing():
    get, asked = by_ref("{}", theirs())
    assert schema_pin.check_upstream(get) == []
    assert asked == [
        "https://raw.githubusercontent.com/roykollensvendsen/claude-voice-mobile/main/docs/plugins/sms/sms-since-answer.json"
    ]


def test_the_phone_s_main_moving_on_says_how_to_move_the_pin():
    said = schema_pin.check_upstream(lambda _url: "{}")
    assert "no longer the file pinned" in said[0]
    assert schema_pin.digest("{}") in said[1]
    assert "put the new commit and digest in the pin" in said[1]


def test_the_phone_s_main_moving_on_is_a_reminder_not_a_failure(monkeypatch, capsys, tmp_path):
    get, asked = by_ref(theirs(), "{}")
    monkeypatch.setattr(schema_pin, "fetch", get)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert schema_pin.main(["upstream"]) == 0
    out = capsys.readouterr().out
    assert out.count("::notice ") == 1
    assert schema_pin.digest("{}") in out
    assert "put the new commit and digest in the pin" in summary.read_text(encoding="utf-8")
    assert len(asked) == 2, "both the pinned commit and main are asked"


def test_the_reminder_needs_no_step_summary(monkeypatch, capsys):
    get, _ = by_ref(theirs(), "{}")
    monkeypatch.setattr(schema_pin, "fetch", get)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert schema_pin.main(["upstream"]) == 0
    assert "::notice " in capsys.readouterr().out


def test_the_command_line_fails_when_the_pin_is_wrong(monkeypatch, capsys):
    get, asked = by_ref("{}", theirs())
    monkeypatch.setattr(schema_pin, "fetch", get)
    assert schema_pin.main(["upstream"]) == 1
    assert "the pin is wrong" in capsys.readouterr().err
    assert len(asked) == 1, "main is not asked about a pin that is wrong"


def test_the_command_line_is_quiet_when_the_pin_is_true_and_main_has_not_moved(monkeypatch, capsys):
    get, _ = by_ref(theirs(), theirs())
    monkeypatch.setattr(schema_pin, "fetch", get)
    assert schema_pin.main(["upstream"]) == 0
    out = capsys.readouterr().out
    assert "::notice" not in out
    assert "matches the pin" in out


def test_the_command_line_says_the_copy_matches(capsys):
    assert schema_pin.main(["copy"]) == 0
    assert "matches the pin" in capsys.readouterr().out


def test_the_nightly_job_checks_the_phone_s_main_and_nothing_else():
    job = yaml.safe_load((ROOT / ".github/workflows/schema-pin.yml").read_text(encoding="utf-8"))
    triggers = job.get("on") or job.get(True)
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert job["permissions"] == {"contents": "read"}
    [steps] = [j["steps"] for j in job["jobs"].values()]
    assert steps[-1]["run"] == "python3 scripts/schema_pin.py upstream"


def test_the_pin_file_is_plain_json():
    assert json.loads(schema_pin.PIN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("what", ["copy", "upstream"])
def test_the_command_line_takes_only_copy_or_upstream(what):
    with pytest.raises(SystemExit):
        schema_pin.main([what + "x"])
