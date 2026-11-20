"""How the checks' runs share GitHub's queue."""

import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).parent.parent
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
#: The nightly schema check keeps one GitHub issue, so its runs queue one after another.
ONE_AT_A_TIME = ROOT / ".github/workflows/schema-pin.yml"


def _concurrency(workflow: pathlib.Path) -> dict[str, object]:
    return yaml.safe_load(workflow.read_text(encoding="utf-8"))["concurrency"]


@pytest.mark.parametrize("workflow", [w for w in WORKFLOWS if w != ONE_AT_A_TIME], ids=lambda p: p.name)
def test_a_newer_push_stops_only_a_pull_requests_stale_run(workflow):
    """One group per ref keeps one waiting run, so a main commit between two others got none.

    A pull request's runs share its ref's group, and a newer push cancels the stale one.
    Every other run is keyed by its own run id, so it is never cancelled and never skipped.
    """
    assert _concurrency(workflow) == {
        "group": "${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }


def test_a_run_by_hand_waits_for_the_nightly_run_so_only_one_issue_is_opened():
    """Two schema runs at once could each find no issue and each open one; one group queues them."""
    assert _concurrency(ONE_AT_A_TIME) == {"group": "${{ github.workflow }}", "cancel-in-progress": False}
