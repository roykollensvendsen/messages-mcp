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
    assert "not the file pinned at 58c8e54ac185" in said
    assert schema_pin.digest(copy.read_text(encoding="utf-8")) in said


def test_a_copy_holding_a_real_looking_number_is_refused(tmp_path):
    copy = tmp_path / "copy.json"
    copy.write_text(schema_pin.COPY.read_text(encoding="utf-8").replace("+<country><number>", NUMBER))
    said = schema_pin.check_copy(copy)
    assert any("international form" in line for line in said)


def test_the_phone_s_main_matching_the_pin_says_nothing():
    """The phone's own file holds a real-looking number where the copy has the placeholder."""
    theirs = schema_pin.COPY.read_text(encoding="utf-8").replace("+<country><number>", NUMBER)
    asked = []

    def get(url):
        asked.append(url)
        return theirs

    assert schema_pin.check_upstream(get) == []
    assert asked == [
        "https://raw.githubusercontent.com/roykollensvendsen/claude-voice-mobile/main/docs/plugins/sms/sms-since-answer.json"
    ]


def test_the_phone_s_main_moving_on_says_how_to_move_the_pin():
    said = schema_pin.check_upstream(lambda _url: "{}")
    assert "no longer the file pinned" in said[0]
    assert schema_pin.digest("{}") in said[1]
    assert "put the new commit and digest in the pin" in said[1]


def test_the_command_line_fails_when_the_phone_s_main_moved(monkeypatch, capsys):
    monkeypatch.setattr(schema_pin, "fetch", lambda _url: "{}")
    assert schema_pin.main(["upstream"]) == 1
    assert "no longer the file pinned" in capsys.readouterr().err


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
