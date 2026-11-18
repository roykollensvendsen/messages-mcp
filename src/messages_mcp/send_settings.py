"""send.toml: the undo delay, set only on this laptop (messages-v1 §8).

No tool and no voice call reads or changes it; the secret table write-denies the file for
every role. A missing file or setting means the default; a value under the floor is refused
at load, naming the floor and the file.
"""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib

#: Seconds a confirmed message is held before it goes, when nothing else is set.
DEFAULT_DELAY = 10
#: The shortest hold there may be; anything lower is refused at load.
FLOOR = 5
KEY = "delay_seconds"


class SendSettingsError(ValueError):
    """send.toml says something that cannot be used; the message names the file."""


def load_delay(path: pathlib.Path) -> float:
    """The undo delay in seconds, from `path`, or the default when it says none."""
    try:
        table = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_DELAY
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as broken:
        msg = f"{path} is not valid TOML ({broken}); fix it, or remove it for the {DEFAULT_DELAY} second default."
        raise SendSettingsError(msg) from broken
    unknown = sorted(set(table) - {KEY})
    if unknown:
        msg = f"{path} has settings messages-mcp does not know: {', '.join(unknown)}. Only {KEY} is read."
        raise SendSettingsError(msg)
    delay = table.get(KEY, DEFAULT_DELAY)
    if isinstance(delay, bool) or not isinstance(delay, int | float):
        msg = f"{path}: {KEY} must be a number of seconds."
        raise SendSettingsError(msg)
    # RULE: a delay under the floor is refused at load
    if delay < FLOOR:
        msg = f"{path}: {KEY} is {delay}, under the floor of {FLOOR} seconds; set {FLOOR} or more."
        raise SendSettingsError(msg)
    return float(delay)
