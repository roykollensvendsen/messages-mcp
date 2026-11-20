"""Pin our copy of the phone's sms_since answer schema to one claude-voice-mobile commit.

`tests/sms-since-answer.json` is a copy of claude-voice-mobile's
`docs/plugins/sms/sms-since-answer.json` (ADR-CVM-010), which sms-bridge is built against. Our
private-data scan refuses any number in international form, so the copy writes each one as
`+<country><number>`. The pin, `tests/sms-since-answer.pin.json`, names the commit the copy
came from and the sha256 of the file with every such number written that way. So the copy and
the phone's own file are compared by the same digest, and no digits are kept here.

    python scripts/schema_pin.py copy       the copy matches its pin; offline, and in the tests
    python scripts/schema_pin.py upstream   the file on the phone's main matches the pin; nightly

Moving the pin is one pull request: copy the new file, written the same way, and set the
commit and the digest this prints in the pin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.request
from typing import TYPE_CHECKING

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from private_data_scan import PHONE

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = pathlib.Path(__file__).parent.parent
COPY = ROOT / "tests" / "sms-since-answer.json"
PIN = ROOT / "tests" / "sms-since-answer.pin.json"
PLACEHOLDER = "+<country><number>"
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
#: Seconds for the one fetch the nightly job makes.
TIMEOUT = 30


def normalised(text: str) -> str:
    """`text` with every number in international form written `+<country><number>`."""
    return PHONE.sub(PLACEHOLDER, text)


def digest(text: str) -> str:
    """The sha256 the pin keeps: of `text`, normalised."""
    return hashlib.sha256(normalised(text).encode("utf-8")).hexdigest()


def pinned(path: pathlib.Path = PIN) -> dict[str, str]:
    """The pin: the repository, the file's path in it, the commit, and the digest."""
    pin = json.loads(path.read_text(encoding="utf-8"))
    return {key: str(pin[key]) for key in ("repo", "path", "commit", "sha256")}


def check_copy(copy: pathlib.Path = COPY, pin_file: pathlib.Path = PIN) -> list[str]:
    """What is wrong with the copy against its pin; empty when it matches."""
    pin = pinned(pin_file)
    text = copy.read_text(encoding="utf-8")
    said = []
    if normalised(text) != text:
        said.append(f"{copy.name} holds a number in international form; write it {PLACEHOLDER}")
    if (found := digest(text)) != pin["sha256"]:
        said.append(f"{copy.name} is not the file pinned at {pin['commit'][:12]}: its digest is {found}")
    return said


def fetch(url: str) -> str:
    """The text at `url`."""
    with urllib.request.urlopen(url, timeout=TIMEOUT) as answer:  # noqa: S310 - a fixed https URL
        return answer.read().decode("utf-8")


def check_upstream(get: Callable[[str], str] = fetch, pin_file: pathlib.Path = PIN) -> list[str]:
    """What has moved in the phone's file on main since the pin; empty when nothing has."""
    pin = pinned(pin_file)
    url = RAW.format(repo=pin["repo"], ref="main", path=pin["path"])
    found = digest(get(url))
    if found == pin["sha256"]:
        return []
    return [
        f"{pin['path']} on {pin['repo']}'s main is no longer the file pinned at {pin['commit'][:12]}.",
        (
            f"Its digest is now {found}. Read what changed, and change sms-bridge if a field did. Then copy"
            f" the file to tests/sms-since-answer.json with numbers written {PLACEHOLDER}, and put the new"
            " commit and digest in the pin."
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    """Check the copy or the phone's main; 1, with what moved, when it does not match."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("what", choices=("copy", "upstream"))
    args = parser.parse_args(argv)
    said = check_copy() if args.what == "copy" else check_upstream(fetch)
    for line in said:
        sys.stderr.write(line + "\n")
    if not said:
        sys.stdout.write(f"The {args.what} matches the pin.\n")
    return 1 if said else 0


if __name__ == "__main__":
    sys.exit(main())
