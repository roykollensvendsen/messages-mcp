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


def test_a_device_list_that_is_a_link_is_refused(tmp_path):
    """A link could point the door at a list someone else keeps; a link to a list that is gone too."""
    real = _written(tmp_path, LIST)
    (tmp_path / "linked.toml").symlink_to(real)
    with pytest.raises(door_list.DoorListError, match="is a link"):
        door_list.allowed(tmp_path / "linked.toml")
    (tmp_path / "dangling.toml").symlink_to(tmp_path / "gone.toml")
    with pytest.raises(door_list.DoorListError, match="is a link"):
        door_list.allowed(tmp_path / "dangling.toml")


def test_a_device_list_that_belongs_to_someone_else_is_refused(tmp_path, monkeypatch):
    path = _written(tmp_path, LIST)
    monkeypatch.setattr(door_list.os, "getuid", lambda: path.stat().st_uid + 1)
    with pytest.raises(door_list.DoorListError, match="belongs to someone else"):
        door_list.allowed(path)


def test_a_list_whose_folder_cannot_be_opened_is_refused(tmp_path):
    folder = tmp_path / "shut"
    folder.mkdir()
    _written(folder, LIST)
    folder.chmod(0)
    try:
        with pytest.raises(door_list.DoorListError, match="cannot be read"):
            door_list.allowed(folder / "door.toml")
    finally:
        folder.chmod(0o700)


def test_a_device_list_is_read_through_the_descriptor_that_was_checked(tmp_path, monkeypatch):
    """A list swapped for a link between its check and its read is still refused (quality's n2 on #3)."""
    import pathlib

    planted = _written(tmp_path, '[[device]]\nname = "planted"\nid = "nFAKE0PLANTED0ID"\n')
    (tmp_path / "checked").mkdir()
    looked_at = _written(tmp_path / "checked", LIST)
    swapped = tmp_path / "swapped.toml"
    swapped.symlink_to(planted)
    # What a check made just before the swap would have seen: a plain file of the owner's.
    monkeypatch.setattr(pathlib.Path, "lstat", lambda _self: looked_at.stat())
    with pytest.raises(door_list.DoorListError, match="is a link"):
        door_list.allowed(swapped)


def test_a_device_list_that_is_not_a_plain_file_is_refused_without_waiting(tmp_path):
    """A pipe in the list's place is refused at once; opening it never waits for a writer."""
    import os
    import threading

    pipe = tmp_path / "door.toml"
    os.mkfifo(pipe, 0o600)
    got = []

    def read():
        try:
            door_list.allowed(pipe)
        except door_list.DoorListError as refused:
            got.append(str(refused))

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(2)
    waited = reader.is_alive()
    if waited:
        # Let the stuck read go: a writer that opens and closes gives it an end.
        os.close(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))
        reader.join(2)
    assert not waited, "the door waited on a pipe in the list's place"
    assert got and "not a plain file" in got[0]
