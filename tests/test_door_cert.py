"""Renewing the door's certificate: for the laptop's own name, a month ahead, with a key only the owner reads."""

import pytest

from messages_mcp import door_cert
from messages_mcp.localapi import LocalAPIError

NAME = "made-up-laptop.example.invalid"


class StubAPI:
    def __init__(self, name=NAME):
        self.name = name

    async def own_name(self):
        if self.name is None:
            msg = "tailscaled did not answer"
            raise LocalAPIError(msg)
        return self.name


def _renewed(tmp_path, api, *, key_mode=0o644, code=0):
    asked = []
    cert, key = tmp_path / "door.crt", tmp_path / "door.key"

    def run(command):
        asked.append(command)
        cert.write_text("made-up certificate")
        key.write_text("made-up key")
        key.chmod(key_mode)
        return code

    door_cert.renew(api, cert, key, run=run)
    return asked, key


def test_the_certificate_is_renewed_for_this_laptop_by_its_tailnet_name(tmp_path):
    asked, _ = _renewed(tmp_path, StubAPI())
    assert asked == [
        [
            "tailscale",
            "cert",
            "--min-validity",
            "720h",
            "--cert-file",
            str(tmp_path / "door.crt"),
            "--key-file",
            str(tmp_path / "door.key"),
            NAME,
        ]
    ]


def test_the_door_key_is_readable_by_the_owner_only(tmp_path):
    _, key = _renewed(tmp_path, StubAPI(), key_mode=0o644)
    assert key.stat().st_mode & 0o777 == 0o600


def test_a_failed_renewal_says_so(tmp_path):
    with pytest.raises(door_cert.CertError, match="tailscale cert failed"):
        _renewed(tmp_path, StubAPI(), code=1)
    with pytest.raises(door_cert.CertError, match="did not answer"):
        _renewed(tmp_path, StubAPI(name=None))
