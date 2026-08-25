"""Contract tests for real worker SIGKILL recovery automation."""

import pytest

from scripts.run_research_worker_sigkill_e2e import (
    assert_sigkill_state,
    validate_recovered_run,
)


def _events() -> list[dict]:
    return [
        {"sequence": 1, "payload": {}},
        {"sequence": 2, "payload": {"recovery": "REENQUEUED_STALLED_STAGE"}},
        {"sequence": 3, "payload": {}},
    ]


def test_sigkill_requires_linux_exit_code_137() -> None:
    assert_sigkill_state({"Status": "exited", "ExitCode": 137})

    with pytest.raises(RuntimeError, match="not terminated by SIGKILL"):
        assert_sigkill_state({"Status": "exited", "ExitCode": 0})


def test_recovery_requires_one_transition_and_one_watchdog_event() -> None:
    report = validate_recovered_run(
        {"state": "COMPLETED", "state_version": 1},
        _events(),
    )

    assert report == {"event_count": 3, "recovery_sequence": 2, "state_version": 1}


@pytest.mark.parametrize(
    ("run", "events", "message"),
    [
        ({"state": "COMPLETED", "state_version": 2}, _events(), "exactly once"),
        (
            {"state": "COMPLETED", "state_version": 1},
            [_events()[0], _events()[2]],
            "not contiguous",
        ),
        (
            {"state": "COMPLETED", "state_version": 1},
            [{"sequence": 1, "payload": {}}, {"sequence": 2, "payload": {}}],
            "exactly one watchdog",
        ),
    ],
)
def test_recovery_rejects_ambiguous_or_duplicate_outcome(run, events, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        validate_recovered_run(run, events)
