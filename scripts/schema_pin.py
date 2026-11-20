"""Pin our copy of the phone's sms_since answer schema to one claude-voice-mobile commit.

`tests/sms-since-answer.json` is a copy of claude-voice-mobile's
`docs/plugins/sms/sms-since-answer.json` (ADR-CVM-010), which sms-bridge is built against. Our
private-data scan refuses any number in international form, so the copy writes each one as
`+<country><number>`. The pin, `tests/sms-since-answer.pin.json`, names the commit the copy
came from and the sha256 of the file with every such number written that way. So the copy and
the phone's own file are compared by the same digest, and no digits are kept here.

    python scripts/schema_pin.py copy       the copy matches its pin; offline, and in the tests
    python scripts/schema_pin.py upstream   the pin is true, and whether the phone's main moved; nightly

`upstream` fails when the file at the pinned commit does not have the pinned digest: then the
pin itself is wrong. The phone's main moving on is only a reminder (no-duplicates n8): exit 0,
with a `::notice::` line and, on GitHub, a step summary saying how to move the pin.

With `--issue`, as the nightly job runs it, the reminder is also one GitHub issue, made with
`gh`: opened when main moves on, its text kept current while it stays moved, and closed when the
pin catches up. It names only the file, the pinned commit and the new digest.

Moving the pin is one pull request: copy the new file, written the same way, and set the
commit and the digest this prints in the pin.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
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
#: The one issue the nightly job keeps while the phone's main has moved on from the pin.
ISSUE_TITLE = "The phone's text schema has moved on from the pin"


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


def url_at(pin: dict[str, str], ref: str) -> str:
    """Where the phone's file is read at `ref`, a commit or a branch."""
    return RAW.format(repo=pin["repo"], ref=ref, path=pin["path"])


def check_pinned(get: Callable[[str], str] = fetch, pin_file: pathlib.Path = PIN) -> list[str]:
    """What is wrong with the pin itself: the file at its commit with another digest; empty when true."""
    pin = pinned(pin_file)
    # RULE: the pin is proved against the file at its own commit
    found = digest(get(url_at(pin, pin["commit"])))
    if found == pin["sha256"]:
        return []
    return [f"the pin is wrong: {pin['path']} at {pin['commit'][:12]} has the digest {found}, not {pin['sha256']}"]


def check_upstream(get: Callable[[str], str] = fetch, pin_file: pathlib.Path = PIN) -> list[str]:
    """How to move the pin, when the phone's file on main has moved on since it; empty when not."""
    pin = pinned(pin_file)
    found = digest(get(url_at(pin, "main")))
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


def _remind(said: list[str]) -> None:
    """Say that main moved as a GitHub notice and, when there is one, in the step's summary."""
    sys.stdout.write(f"::notice title=The phone's schema moved on::{' '.join(said)}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with pathlib.Path(summary).open("a", encoding="utf-8") as page:
            page.write("## The phone's text schema moved on\n\n" + "\n\n".join(said) + "\n")


def issue_steps(moved: list[str], open_issues: list[dict[str, object]], pin: dict[str, str]) -> list[list[str]]:
    """The `gh` calls that keep one issue in step with whether main has moved; none when it is."""
    ours = sorted(int(str(issue["number"])) for issue in open_issues if issue.get("title") == ISSUE_TITLE)
    if moved:
        body = "\n\n".join(moved)
        # RULE: one issue is kept while the schema stays moved
        if ours:
            return [["gh", "issue", "edit", str(ours[0]), "--body", body]]
        return [["gh", "issue", "create", "--title", ISSUE_TITLE, "--body", body]]
    said = f"The pin matches {pin['path']} on main again, at {pin['commit'][:12]}."
    # RULE: the issue is closed when the pin catches up
    return [["gh", "issue", "close", str(number), "--comment", said] for number in ours]


def _gh(argv: list[str]) -> str:
    """Run one `gh` call; its output."""
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout


def keep_issue(moved: list[str], run: Callable[[list[str]], str] = _gh, pin_file: pathlib.Path = PIN) -> None:
    """Open, update or close the one issue, as `issue_steps` says."""
    listed = run(["gh", "issue", "list", "--state", "open", "--limit", "100", "--json", "number,title"])
    for step in issue_steps(moved, json.loads(listed), pinned(pin_file)):
        run(step)


def main(argv: list[str] | None = None) -> int:
    """Check the copy, or the pin and the phone's main; 1, with what is wrong, when the copy or pin is."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("what", choices=("copy", "upstream"))
    parser.add_argument("--issue", action="store_true", help="upstream: keep one GitHub issue while main has moved")
    args = parser.parse_args(argv)
    wrong = check_copy() if args.what == "copy" else check_pinned(fetch)
    for line in wrong:
        sys.stderr.write(line + "\n")
    if wrong:
        return 1
    sys.stdout.write(
        "The copy matches the pin.\n" if args.what == "copy" else "The file at the pinned commit matches the pin.\n"
    )
    if args.what != "upstream":
        return 0
    if moved := check_upstream(fetch):
        # RULE: the phone's main moving on is a reminder not a failure
        _remind(moved)
    if args.issue:
        keep_issue(moved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
