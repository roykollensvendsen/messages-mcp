"""send.toml: the undo delay is set only on this laptop, 10 s by default and never under 5 s."""

import pytest

from messages_mcp.send_settings import DEFAULT_DELAY, FLOOR, SendSettingsError, load_delay


def test_a_missing_file_means_the_default_delay(tmp_path):
    assert load_delay(tmp_path / "send.toml") == DEFAULT_DELAY == 10


def test_a_missing_setting_means_the_default_delay(tmp_path):
    path = tmp_path / "send.toml"
    path.write_text("")
    assert load_delay(path) == DEFAULT_DELAY


def test_a_delay_at_or_over_the_floor_is_kept(tmp_path):
    path = tmp_path / "send.toml"
    path.write_text("delay_seconds = 5\n")
    assert load_delay(path) == FLOOR == 5
    path.write_text("delay_seconds = 30\n")
    assert load_delay(path) == 30


@pytest.mark.parametrize("value", ["4", "4.9", "0", "-10"])
def test_a_delay_under_the_floor_is_refused_at_load(tmp_path, value):
    path = tmp_path / "send.toml"
    path.write_text(f"delay_seconds = {value}\n")
    with pytest.raises(SendSettingsError) as refused:
        load_delay(path)
    assert "5 seconds" in str(refused.value)
    assert str(path) in str(refused.value)


@pytest.mark.parametrize("text", ['delay_seconds = "ten"\n', "delay_seconds = true\n", "delay = 10\n", "not toml ["])
def test_a_setting_that_is_not_a_number_of_seconds_is_refused(tmp_path, text):
    path = tmp_path / "send.toml"
    path.write_text(text)
    with pytest.raises(SendSettingsError) as refused:
        load_delay(path)
    assert str(path) in str(refused.value)
