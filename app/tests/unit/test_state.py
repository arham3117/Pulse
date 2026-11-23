"""Table-driven incident state machine tests: S = success, F = failure."""

from datetime import UTC, datetime, timedelta

import pytest

from pulse.worker.state import MonitorState, Transition, apply_check

T0 = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
NEW = MonitorState("unknown", 0, None, None, has_open_incident=False)


def run(sequence: str, threshold: int = 3, state: MonitorState = NEW):
    statuses, transitions = [], []
    for i, ch in enumerate(sequence.split()):
        out = apply_check(
            state,
            success=ch == "S",
            checked_at=T0 + timedelta(minutes=i),
            error=None if ch == "S" else f"err{i}",
            failure_threshold=threshold,
        )
        state = out.state
        statuses.append(state.status)
        transitions.append(out.transition)
    return state, statuses, transitions


OPEN, R, N = Transition.OPEN_INCIDENT, Transition.RESOLVE_INCIDENT, Transition.NONE


@pytest.mark.parametrize(
    ("seq", "threshold", "statuses", "transitions"),
    [
        ("S S F F F S", 3, ["up", "up", "up", "up", "down", "up"], [N, N, N, N, OPEN, R]),
        ("F F F", 3, ["unknown", "unknown", "down"], [N, N, OPEN]),
        ("F F S F F", 3, ["unknown", "unknown", "up", "up", "up"], [N, N, N, N, N]),
        ("S F", 1, ["up", "down"], [N, OPEN]),
        ("F F F F F", 3, ["unknown", "unknown", "down", "down", "down"], [N, N, OPEN, N, N]),
        (
            "F F F S F F F",
            3,
            ["unknown"] * 2 + ["down", "up", "up", "up", "down"],
            [N, N, OPEN, R, N, N, OPEN],
        ),
        ("S S S", 3, ["up", "up", "up"], [N, N, N]),
    ],
)
def test_sequences(seq, threshold, statuses, transitions) -> None:
    _, got_statuses, got_transitions = run(seq, threshold)
    assert got_statuses == statuses
    assert got_transitions == transitions


def test_incident_start_is_first_failure_of_streak() -> None:
    state, _, _ = run("S S F F F")
    assert state.failing_since == T0 + timedelta(minutes=2)
    assert state.failing_error == "err2"
    assert state.consecutive_failures == 3
    assert state.has_open_incident


def test_success_clears_streak() -> None:
    state, _, _ = run("F F S")
    assert state.consecutive_failures == 0
    assert state.failing_since is None
    assert state.failing_error is None


def test_existing_open_incident_is_not_reopened() -> None:
    down = MonitorState("down", 5, T0, "boom", has_open_incident=True)
    out = apply_check(down, success=False, checked_at=T0, error="x", failure_threshold=3)
    assert out.transition is Transition.NONE
    assert out.state.consecutive_failures == 6
    assert out.state.failing_since == T0
