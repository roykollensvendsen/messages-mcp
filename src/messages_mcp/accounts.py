"""What the voice calls each bridged Messenger login.

While only one login has ever been seen, answers name no account at all. From the second on,
each login keeps the number it got the first time it was seen, stored in the state folder, so
a deleted or re-created chat never moves a number. The owner may give a login a name in the
config's [accounts] table, and that name is said instead. A chat with no login is "account ?".
The login IDs are private: they stay in that file and the owner's config, never in an answer.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from messages_mcp import bridge_config

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Iterable

UNKNOWN = "account ?"


class Accounts:
    """The labels, kept in `path`, with the owner's `names` for some logins."""

    def __init__(self, path: pathlib.Path, names: dict[str, str]) -> None:
        """Read the numbers already given out from `path`."""
        self.path = path
        self.names = names
        self._numbers, self._announced = self._read()

    def labels(self, receivers: Iterable[str]) -> tuple[dict[str, str | None], str | None]:
        """Each receiver's label, and the one-time news that a second login appeared."""
        seen = set(receivers)
        before = len(self._numbers)
        for receiver in sorted(seen - {""} - set(self._numbers)):
            # RULE: a login keeps the number it got the first time it was seen
            self._numbers[receiver] = max(self._numbers.values(), default=0) + 1
        news = None
        if len(self._numbers) > 1 and not self._announced:
            second = next(r for r, n in self._numbers.items() if n == 2)  # noqa: PLR2004 - the second login
            news = f"A second Messenger account is now bridged; it's called {self._label(second)}."
            self._announced = True
        if len(self._numbers) != before or news:
            self._write()
        # RULE: one login means no label
        several = len(self._numbers) > 1
        return {r: (self._label(r) if several or not r else None) for r in seen}, news

    def _label(self, receiver: str) -> str:
        if not receiver:
            return UNKNOWN
        return self.names.get(receiver) or f"account {self._numbers[receiver]}"

    def _read(self) -> tuple[dict[str, int], bool]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            numbers = {str(r): int(n) for r, n in data["numbers"].items()}
            return numbers, bool(data.get("announced", False))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}, False

    def _write(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        text = json.dumps({"numbers": self._numbers, "announced": self._announced}, sort_keys=True)
        bridge_config.write_private(self.path, text)
