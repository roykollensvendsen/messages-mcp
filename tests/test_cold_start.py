"""A cold first start shows the voice its tools within the bridge's start limit.

The bridge starts a plugin as a fresh process and gives up if the tools are not listed in
time, and the voice then sees nothing. "Cold" means a new interpreter with no compiled
bytecode to reuse, so every heavy import is paid in full, as on the first start after an
update.

The time that counts is the server's own CPU time, not the clock: on a busy machine the
clock measures the other work too, and a test on it failed with no change to the code. A
start that waits instead of working costs no CPU, so two more tests close that gap: listing
the tools asks the inbox nothing, and no code sleeps. A loose limit on the clock, 60 s, ends a
start that hangs. The bridge's own 15 s limit is on the clock, in real use.

These tests count the CPU of every child process this one waited for, so they assume the
suite runs one test at a time; with pytest-xdist, run this file alone.
"""

import ast
import asyncio
import os
import pathlib
import resource
import sys
import time

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from messages_mcp.accounts import Accounts
from messages_mcp.server import TOOLS, build_server
from messages_mcp.service import MessagesService

#: claude-voice's ``plugins.START_SECONDS``. The bridge owns the figure; it is copied here
#: because the bridge is not a dependency of this package.
BRIDGE_START_SECONDS = 15.0
#: The server's own work may use half of it; the other half is for a machine that is busy.
CPU_SECONDS = BRIDGE_START_SECONDS / 2
#: A loose limit on the clock as well: CPU time misses a start that waits or hangs.
WALL_SECONDS = 60.0
SRC = pathlib.Path(__file__).parent.parent / "src"
#: Starts the server as ``messages-mcp serve --simulate`` does, after ``first``.
LAUNCH = (
    "import runpy, sys\n"
    "sys.argv = ['messages-mcp', 'serve', '--simulate']\n"
    "runpy.run_module('messages_mcp.cli', run_name='__main__')"
)


def _children_cpu():
    used = resource.getrusage(resource.RUSAGE_CHILDREN)
    return used.ru_utime + used.ru_stime


async def _start(env, first, wall):
    params = StdioServerParameters(command=sys.executable, args=["-c", first + "\n" + LAUNCH], env=env)
    # Outside the client, so that a hang ends as a plain TimeoutError after the server is stopped.
    async with (
        asyncio.timeout(wall),
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
    return sorted(t.name for t in tools.tools)


def cold_start(tmp_path, first="", wall=WALL_SECONDS):
    """The tools a cold start lists, and the CPU seconds the server spent, once it has exited.

    A child's CPU time is counted only once it has been waited for; the client closes the
    server and waits for it before it returns, so the figure is whole.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC), env.get("PYTHONPATH")]))
    # An empty bytecode folder: nothing compiled before can be reused.
    env["PYTHONPYCACHEPREFIX"] = str(tmp_path / "pycache")
    before, started = _children_cpu(), time.monotonic()
    names = asyncio.run(_start(env, first, wall))
    cpu = _children_cpu() - before
    print(f"cold start: {cpu:.1f} s of CPU, {time.monotonic() - started:.1f} s on the clock")
    assert cpu > 0, "the server's CPU time was never counted; it was not waited for"
    return names, cpu


@pytest.mark.slow
def test_a_cold_first_start_lists_the_tools_within_the_bridges_start_limit(tmp_path):
    names, cpu = cold_start(tmp_path)
    assert names == sorted(TOOLS)
    assert cpu < CPU_SECONDS, f"a cold start used {cpu:.1f} s of CPU"


@pytest.mark.slow
def test_a_start_that_hangs_is_stopped_by_the_clock(tmp_path):
    """Proof the clock guard ends a start that waits: CPU time alone would never see it."""
    with pytest.raises(TimeoutError):
        cold_start(tmp_path, first="import time\ntime.sleep(5)", wall=1.0)


@pytest.mark.slow
def test_a_heavy_start_is_caught_by_the_cpu_it_uses(tmp_path):
    """Proof the measure sees the server's own work: a start that burns CPU first is over."""
    burn = "import time\nt = time.process_time()\nwhile time.process_time() - t < 1.5:\n    pass"
    _, cpu = cold_start(tmp_path, first=burn)
    assert cpu >= 1.5


class _Recording:
    """An inbox that keeps every read and answers none."""

    def __init__(self):
        self.asked = 0

    async def chats(self):
        self.asked += 1
        raise OSError


def _service(inbox):
    return MessagesService(inbox, Accounts(SRC / "never-written.json", {}))


def test_listing_the_tools_asks_the_inbox_nothing():
    """A start that waited on Synapse would cost no CPU, so it is refused here instead."""
    inbox = _Recording()
    assert sorted(t.name for t in asyncio.run(build_server(_service(inbox)).list_tools())) == sorted(TOOLS)
    assert inbox.asked == 0


def test_a_read_is_seen_by_the_recording_inbox():
    """Proof the check above can fail: asking a tool does reach the inbox."""
    inbox = _Recording()
    asyncio.run(build_server(_service(inbox)).call_tool("messages_unread", {}))
    assert inbox.asked == 1


#: The only functions that may wait, each with its reason. None of them is on the way to a first answer.
MAY_SLEEP = {
    # matrix-door's watch waits a minute between asking whether Funnel or serve opened a way
    # round it. It runs beside a door that is already listening, never before it.
    ("door.py", "_watch"),
    # The outbox's undo hold waits the delay after the owner says "send", never during a start.
    ("outbox.py", "__init__"),
    # sms-bridge waits between polls of the phone, in its own loop, never in messages-mcp's start.
    ("sms_bridge.py", "run"),
}


def test_no_code_sleeps():
    """A sleep costs no CPU either, so none may hide in the start."""
    sleeps = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        allowed = {
            id(inner)
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef) and (path.name, fn.name) in MAY_SLEEP
            for inner in ast.walk(fn)
        }
        sleeps += [
            f"{path.name}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "sleep" and id(node) not in allowed
        ]
    assert sleeps == []


def test_each_function_that_may_sleep_still_exists():
    """A stale allowance would let the next sleep put there pass unseen."""
    defined = {
        (path.name, fn.name)
        for path in sorted(SRC.rglob("*.py"))
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert defined >= MAY_SLEEP
