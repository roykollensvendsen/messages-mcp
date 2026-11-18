"""The owner's device list: only the owner writes it, and a broken one never passes as open."""

import pytest

from messages_mcp import door_list

LIST = (
    '[[device]]\nname = "phone"\nid = "nFAKE0PHONE0CNTRL"\n\n[[device]]\nname = "laptop"\nid = "nFAKE0LAPTOP0CNTRL"\n'
)


def _written(tmp_path, text, mode=0o600):
    path = tmp_path / "door.toml"
    path.write_text(text)
    path.chmod(mode)
    return path


def test_the_list_gives_each_devices_stable_id(tmp_path):
    assert door_list.allowed(_written(tmp_path, LIST)) == {"nFAKE0PHONE0CNTRL", "nFAKE0LAPTOP0CNTRL"}


def test_no_list_lets_nobody_in(tmp_path):
    assert door_list.allowed(tmp_path / "door.toml") == frozenset()


def test_an_empty_list_lets_nobody_in(tmp_path):
    assert door_list.allowed(_written(tmp_path, "")) == frozenset()


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o620, 0o666])
def test_a_device_list_others_can_read_or_change_is_refused(tmp_path, mode):
    with pytest.raises(door_list.DoorListError, match="chmod 600"):
        door_list.allowed(_written(tmp_path, LIST, mode))


@pytest.mark.parametrize("text", ["[[device", 'device = "x"\n', '[[device]]\nname = "phone"\n', "[[device]]\nid = 7\n"])
def test_a_list_that_is_not_a_list_of_devices_is_refused_with_the_fix(tmp_path, text):
    with pytest.raises(door_list.DoorListError, match="fix it|must hold"):
        door_list.allowed(_written(tmp_path, text))


def test_a_link_to_a_list_that_is_gone_is_refused(tmp_path):
    (tmp_path / "door.toml").symlink_to(tmp_path / "gone.toml")
    with pytest.raises(door_list.DoorListError):
        door_list.allowed(tmp_path / "door.toml")
