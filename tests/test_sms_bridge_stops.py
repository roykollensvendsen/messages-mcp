"""sms-bridge's real poll loop ends cleanly when systemd or the terminal stops it.

The command tests stand in for `cli._run_until_stopped`, so a mutation run never hangs in it.
This runs the real one, in a process of its own: if a stop were not caught, the signal would
end that process, never the test run. The bridge in it is made up: it sends the stop to itself,
then waits, with a bound, for the loop to pass the stop on.
"""

import signal
import sys

import pytest
from bounded import run_bounded

CHILD = """
import asyncio, os, signal, sys
from messages_mcp import cli

class Bridge:
    async def run(self, stop):
        os.kill(os.getpid(), getattr(signal, sys.argv[1]))
        await asyncio.wait_for(stop.wait(), 10)
        print("stopped as asked")

sys.exit(cli._run_until_stopped(Bridge()))
"""


@pytest.mark.parametrize("stopping", [signal.SIGTERM, signal.SIGINT], ids=lambda s: s.name)
def test_sms_bridge_ends_cleanly_when_systemd_or_the_terminal_stops_it(stopping):
    done = run_bounded([sys.executable, "-c", CHILD, stopping.name], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "stopped as asked" in done.stdout
