"""Incident state machine. Pure functions, no I/O, so it is easy to unit test.

Rules:
  * A success resets the failure streak, sets status ``up`` and resolves any open incident.
  * A failure increments ``consecutive_failures``. The first failure of a streak records
    ``failing_since``/``failing_error``.
  * When ``consecutive_failures`` reaches ``failure_threshold`` and no incident is open,
    status becomes ``down`` and an incident opens with ``started_at = failing_since``.
  * Below the threshold the status is unchanged (a single blip doesn't flip ``up`` → ``down``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum


class Transition(StrEnum):
    NONE = "none"
    OPEN_INCIDENT = "open_incident"
    RESOLVE_INCIDENT = "resolve_incident"


@dataclass(frozen=True, slots=True)
class MonitorState:
    status: str  # up | down | unknown | paused
    consecutive_failures: int
    failing_since: datetime | None
    failing_error: str | None
    has_open_incident: bool


@dataclass(frozen=True, slots=True)
class Outcome:
    state: MonitorState
    transition: Transition


def apply_check(
    state: MonitorState,
    *,
    success: bool,
    checked_at: datetime,
    error: str | None,
    failure_threshold: int,
) -> Outcome:
    if success:
        new = replace(
            state,
            status="up",
            consecutive_failures=0,
            failing_since=None,
            failing_error=None,
            has_open_incident=False,
        )
        transition = Transition.RESOLVE_INCIDENT if state.has_open_incident else Transition.NONE
        return Outcome(new, transition)

    failures = state.consecutive_failures + 1
    since = state.failing_since if state.consecutive_failures > 0 else checked_at
    first_error = state.failing_error if state.consecutive_failures > 0 else error
    new = replace(
        state,
        consecutive_failures=failures,
        failing_since=since or checked_at,
        failing_error=first_error or error,
    )
    if failures >= failure_threshold:
        new = replace(new, status="down")
        if not state.has_open_incident:
            return Outcome(replace(new, has_open_incident=True), Transition.OPEN_INCIDENT)
    return Outcome(new, Transition.NONE)
