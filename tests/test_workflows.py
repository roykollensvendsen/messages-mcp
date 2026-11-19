"""How the checks' runs share GitHub's queue."""

import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).parent.parent
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_a_newer_push_stops_only_a_pull_requests_stale_run(workflow):
    """One group per ref keeps one waiting run, so a main commit between two others got none.

    A pull request's runs share its ref's group, and a newer push cancels the stale one.
    Every other run is keyed by its own run id, so it is never cancelled and never skipped.
    """
    concurrency = yaml.safe_load(workflow.read_text(encoding="utf-8"))["concurrency"]
    assert concurrency == {
        "group": "${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}",
        "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
    }
