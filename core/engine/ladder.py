"""Escalation ladder: ok -> checking_in -> caregiver_alerted -> secondary_alerted -> resolved.

Pure rules only. Timed transitions are stamped with the time they were *due*, not the time an
evaluator happened to run, so a late or restarted evaluator produces the same audit trail.
"""

from __future__ import annotations

from core.store.models import Alert, AlertState, AlertTransition, Household

S = AlertState

ALLOWED: dict[AlertState, set[AlertState]] = {
    S.OK: {S.CHECKING_IN, S.CAREGIVER_ALERTED},  # help requests skip straight to the caregiver
    S.CHECKING_IN: {S.CAREGIVER_ALERTED, S.RESOLVED},
    S.CAREGIVER_ALERTED: {S.SECONDARY_ALERTED, S.RESOLVED},
    S.SECONDARY_ALERTED: {S.RESOLVED},
    S.RESOLVED: set(),
}

OPEN = {S.OK, S.CHECKING_IN, S.CAREGIVER_ALERTED, S.SECONDARY_ALERTED}

NOTES = {
    S.CHECKING_IN: "No check-in by the deadline; asking {parent} again",
    S.CAREGIVER_ALERTED: "{parent} did not answer the follow-up; caregiver alerted",
    S.SECONDARY_ALERTED: "Caregiver has not acknowledged; secondary contact alerted",
}


class InvalidTransition(ValueError):
    pass


def transition(alert: Alert, to: AlertState, ts: int, actor: str = "engine", note: str = "") -> AlertTransition:
    if to not in ALLOWED[alert.state]:
        raise InvalidTransition(f"{alert.state.value} -> {to.value} is not allowed")
    return AlertTransition(from_state=alert.state, to_state=to, ts=ts, actor=actor, note=note)


def entered_at(alert: Alert) -> int:
    return alert.history[-1].ts if alert.history else 0


def next_timed(alert: Alert, h: Household) -> tuple[AlertState, int] | None:
    """The next automatic step and when it is due, if any."""
    t0 = entered_at(alert)
    if alert.state == S.CHECKING_IN:
        return S.CAREGIVER_ALERTED, t0 + h.reprompt_min * 60_000
    if alert.state == S.CAREGIVER_ALERTED:
        return S.SECONDARY_ALERTED, t0 + h.secondary_after_min * 60_000
    return None


def note_for(state: AlertState, h: Household) -> str:
    return NOTES.get(state, "").format(parent=h.parent_name)
