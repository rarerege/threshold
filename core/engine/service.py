"""Deterministic routine engine: check-in rule -> decisions -> escalation ladder, all audited in SQLite."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from core.engine import ladder, routine
from core.engine.clock import Clock, SystemClock
from core.store import db
from core.store.models import (
    Alert,
    AlertState,
    CheckIn,
    CheckInStatus,
    Decision,
    DecisionKind,
    DoorLook,
    Event,
    EventType,
    Household,
    Visit,
)

S = AlertState
ANSWERED = {CheckInStatus.OK, CheckInStatus.NEEDS_HELP}


class EngineError(ValueError):
    pass


class Engine:
    def __init__(self, conn: sqlite3.Connection, clock: Clock | None = None):
        self.conn = conn
        self.clock = clock or SystemClock()

    # --- households --------------------------------------------------------

    def ensure_household(self, h: Household) -> Household:
        existing = db.get_household(self.conn, h.id)
        if existing:
            return existing
        db.save_household(self.conn, h)
        return h

    def household(self, household_id: str) -> Household:
        h = db.get_household(self.conn, household_id)
        if h is None:
            raise EngineError(f"unknown household {household_id!r}")
        return h

    # --- rule evaluation ---------------------------------------------------

    def evaluate(self, household_id: str) -> None:
        """Apply every rule that is due up to now. Idempotent; safe to call from any tool or a timer."""
        h = self.household(household_id)
        now = self.clock.now_ms()
        for d, dl in routine.due_deadlines(h, now):
            if db.find_decision(self.conn, h.id, DecisionKind.MISSED_CHECKIN, dl):
                continue
            if h.paused_until is not None and dl <= h.paused_until:
                continue
            start, _ = routine.day_bounds(h, d)
            day_checkins = db.list_checkins(self.conn, h.id, start, now + 1)
            if any(c.status in ANSWERED and c.ts < dl for c in day_checkins):
                continue
            evidence = [c.id for c in day_checkins if c.ts < dl]  # e.g. unanswered prompts
            decision = Decision(household_id=h.id, kind=DecisionKind.MISSED_CHECKIN, evidence=evidence, ts=dl)
            alert = self._open_alert(h, decision, S.CHECKING_IN, dl)
            # Catch up on answers that arrived after the deadline but before this evaluation ran.
            self._advance(h, alert, now, [c for c in day_checkins if c.ts >= dl and c.status in ANSWERED])
        for alert, _ in db.list_alerts(self.conn, h.id, open_only=True):
            self._advance(h, alert, now, [])

    def _open_alert(self, h: Household, decision: Decision, first: AlertState, ts: int, note: str = "") -> Alert:
        db.insert_decision(self.conn, decision)
        alert = Alert(decision_id=decision.id)
        t = ladder.transition(alert, first, ts, note=note or ladder.note_for(first, h))
        alert.history.append(t)
        alert.state = first
        db.insert_alert(self.conn, alert)
        return alert

    def _step(self, alert: Alert, to: AlertState, ts: int, actor: str = "engine", note: str = "") -> None:
        t = ladder.transition(alert, to, ts, actor, note)
        db.record_transition(self.conn, alert.id, t)
        alert.history.append(t)
        alert.state = to

    def _advance(self, h: Household, alert: Alert, until: int, answers: list[CheckIn]) -> None:
        """Replay timed steps and check-in answers in time order up to `until`."""
        answers = sorted(answers, key=lambda c: c.ts)
        while alert.state in ladder.OPEN:
            nxt = ladder.next_timed(alert, h)
            ans = answers[0] if answers else None
            if ans and ans.ts <= until and (nxt is None or ans.ts <= nxt[1]):
                answers.pop(0)
                self._answer(h, alert, ans, until)
                continue
            if nxt and nxt[1] <= until:
                self._step(alert, nxt[0], nxt[1], note=ladder.note_for(nxt[0], h))
                continue
            break

    def _answer(self, h: Household, alert: Alert, c: CheckIn, until: int) -> None:
        """A parent's answer closes a missed_checkin alert; needs_help opens a help alert instead."""
        when = routine.local_time_str(h, c.ts)
        if c.status == CheckInStatus.OK:
            self._step(alert, S.RESOLVED, c.ts, "parent", f"{h.parent_name} checked in at {when}: ok")
        else:
            self._step(alert, S.RESOLVED, c.ts, "parent", f"{h.parent_name} answered at {when}: needs help (see help alert)")
            self._help_alert(h, c, until)

    def _help_alert(self, h: Household, c: CheckIn, until: int) -> Alert:
        for a, d in db.list_alerts(self.conn, h.id, open_only=True):
            if d.kind == DecisionKind.HELP_REQUESTED:
                return a
        decision = Decision(household_id=h.id, kind=DecisionKind.HELP_REQUESTED, evidence=[c.id], ts=c.ts)
        note = f"{h.parent_name} said they need help" + (f": {c.note}" if c.note else "") + "; caregiver alerted"
        alert = self._open_alert(h, decision, S.CAREGIVER_ALERTED, c.ts, note)
        self._advance(h, alert, until, [])
        return alert

    # --- commands ----------------------------------------------------------

    def record_checkin(self, household_id: str, status: CheckInStatus, note: str = "") -> CheckIn:
        h = self.household(household_id)
        self.evaluate(h.id)
        now = self.clock.now_ms()
        c = CheckIn(household_id=h.id, status=status, note=note, ts=now)
        db.insert_checkin(self.conn, c)
        db.upsert_event(self.conn, Event(id=c.id, household_id=h.id, type=EventType.CHECKIN, ts_start=now))
        if status in ANSWERED:
            open_missed = [a for a, d in db.list_alerts(self.conn, h.id, open_only=True)
                           if d.kind == DecisionKind.MISSED_CHECKIN]
            for a in open_missed:
                self._answer(h, a, c, now)
            if status == CheckInStatus.NEEDS_HELP and not open_missed:
                self._help_alert(h, c, now)
        return c

    def acknowledge(self, household_id: str, alert_id: str | None, by: str, note: str = "") -> Alert:
        h = self.household(household_id)
        self.evaluate(h.id)
        open_alerts = db.list_alerts(self.conn, h.id, open_only=True)
        if alert_id is None:
            if not open_alerts:
                raise EngineError("there is no open alert to acknowledge")
            alert = open_alerts[-1][0]
        else:
            alert = db.get_alert(self.conn, alert_id)
            if alert is None or db.get_decision(self.conn, alert.decision_id).household_id != h.id:
                raise EngineError(f"unknown alert {alert_id!r}")
            if alert.state not in ladder.OPEN:
                raise EngineError(f"alert {alert_id} is already {alert.state.value}")
        self._step(alert, S.RESOLVED, self.clock.now_ms(), by or "caregiver", note or "Acknowledged")
        return alert

    def pause(self, household_id: str, hours: float, reason: str) -> Household:
        h = self.household(household_id)
        if hours < 0 or hours > 24 * 14:
            raise EngineError("hours must be between 0 and 336 (14 days)")
        if hours == 0:
            h.paused_until, h.pause_reason = None, ""
        else:
            h.paused_until = self.clock.now_ms() + int(hours * 3_600_000)
            h.pause_reason = reason
        db.save_household(self.conn, h)
        return h

    def add_visit(self, household_id: str, label: str, start: str, end: str, days: list[str]) -> Visit:
        h = self.household(household_id)
        v = Visit(household_id=h.id, label=label, start=start, end=end, days=days)
        if v.end <= v.start:
            raise EngineError("end must be after start (same day)")
        db.insert_visit(self.conn, v)
        return v

    def record_door_look(self, look: DoorLook) -> None:
        db.insert_door_look(self.conn, look)

    def attach_door_event(self, look_id: str, ring_event: dict[str, Any]) -> None:
        look = db.get_door_look(self.conn, look_id)
        a = ring_event.get("attributes", {})
        db.upsert_event(self.conn, Event(id=ring_event["id"], household_id=look.household_id, device_id=look.device_id,
                                         type=EventType.ON_DEMAND, ts_start=a["start"], ts_end=a.get("end")))
        db.set_door_look_event(self.conn, look_id, ring_event["id"])

    # --- queries -----------------------------------------------------------

    def alert_view(self, alert: Alert) -> dict[str, Any]:
        decision = db.get_decision(self.conn, alert.decision_id)
        return self._alert_view(self.household(decision.household_id), alert, decision)

    def _alert_view(self, h: Household, alert: Alert, decision: Decision) -> dict[str, Any]:
        return {
            "alert_id": alert.id,
            "kind": decision.kind.value,
            "state": alert.state.value,
            "opened_at": decision.ts,
            "opened_at_local": routine.local_time_str(h, decision.ts),
            "evidence": decision.evidence,
            "history": [
                {"from_state": t.from_state.value if t.from_state else None, "to_state": t.to_state.value, "ts": t.ts,
                 "time_local": routine.local_time_str(h, t.ts), "actor": t.actor, "note": t.note}
                for t in alert.history
            ],
        }

    def routine_status(self, household_id: str) -> dict[str, Any]:
        h = self.household(household_id)
        self.evaluate(h.id)
        now = self.clock.now_ms()
        today = routine.local_date(h, now)
        start, end = routine.day_bounds(h, today)
        dl = routine.deadline_ms(h, today)
        checkins = db.list_checkins(self.conn, h.id, start, end)
        answered = [c for c in checkins if c.status in ANSWERED]
        last = answered[-1] if answered else None
        paused = h.paused_until is not None and h.paused_until > now
        open_alerts = [self._alert_view(h, a, d) for a, d in db.list_alerts(self.conn, h.id, open_only=True)]

        if last:
            state = "needs_help" if last.status == CheckInStatus.NEEDS_HELP else "checked_in"
        elif now < dl:
            state = "awaiting"
        elif h.paused_until is not None and dl <= h.paused_until:
            state = "paused"
        elif dl < h.monitoring_since:
            state = "not_monitored"
        else:
            state = "missed"

        p = h.parent_name
        if state == "checked_in":
            text = f"Yes. {p} checked in at {routine.local_time_str(h, last.ts)} and said they're ok."
        elif state == "needs_help":
            text = f"{p} checked in at {routine.local_time_str(h, last.ts)} and said they need help."
        elif state == "awaiting":
            text = f"Not yet. {p} usually checks in by {h.checkin_by}; I'll follow up at {routine.local_time_str(h, dl)}."
        elif state == "paused":
            text = f"Monitoring is paused ({h.pause_reason or 'no reason given'}), so today's check-in isn't tracked."
        elif state == "not_monitored":
            text = f"No check-in from {p} today; monitoring started after today's deadline ({routine.local_time_str(h, dl)})."
        else:
            text = f"No. {p} hasn't checked in today; the deadline was {routine.local_time_str(h, dl)}."
        if open_alerts:
            a = open_alerts[-1]
            text += f" Open alert: {a['state'].replace('_', ' ')} since {a['history'][-1]['time_local']}."

        return {
            "household_id": h.id,
            "parent_name": p,
            "date": today.isoformat(),
            "now": now,
            "now_local": routine.local_time_str(h, now),
            "checkin_by": h.checkin_by,
            "deadline": dl,
            "deadline_local": routine.local_time_str(h, dl),
            "routine_state": state,
            "checked_in_today": last is not None,
            "last_checkin": None if not last else {
                "status": last.status.value, "ts": last.ts, "time_local": routine.local_time_str(h, last.ts), "note": last.note},
            "open_alerts": open_alerts,
            "monitoring_paused": paused,
            "paused_until": h.paused_until if paused else None,
            "pause_reason": h.pause_reason if paused else "",
            "text": text,
        }

    def day_summary(self, household_id: str, day: date | None = None) -> dict[str, Any]:
        h = self.household(household_id)
        self.evaluate(h.id)
        day = day or routine.local_date(h, self.clock.now_ms())
        start, end = routine.day_bounds(h, day)
        checkins = db.list_checkins(self.conn, h.id, start, end)
        alerts = [self._alert_view(h, a, d) for a, d in db.list_alerts(self.conn, h.id, since_ms=start, until_ms=end)]
        looks = db.list_door_looks(self.conn, h.id, start, end)
        visits = [v for v in db.list_visits(self.conn, h.id) if routine.weekday(day) in v.days]

        lines = []
        for c in checkins:
            lines.append(f"{routine.local_time_str(h, c.ts)} check-in: {c.status.value.replace('_', ' ')}"
                         + (f" ({c.note})" if c.note else ""))
        for a in alerts:
            path = " → ".join(t["to_state"] for t in a["history"])
            lines.append(f"{a['opened_at_local']} alert ({a['kind'].replace('_', ' ')}): {path}")
        for lk in looks:
            lines.append(f"{routine.local_time_str(h, lk.snapshot_ts)} door snapshot taken")
        for v in visits:
            lines.append(f"expected visit: {v.label} {v.start}–{v.end}")
        text = f"{h.parent_name}'s day ({day.isoformat()}): " + ("; ".join(lines) if lines else "nothing recorded yet.")

        return {
            "household_id": h.id,
            "date": day.isoformat(),
            "checkins": [{"id": c.id, "status": c.status.value, "ts": c.ts, "time_local": routine.local_time_str(h, c.ts),
                          "note": c.note} for c in checkins],
            "alerts": alerts,
            "door_looks": [{"id": lk.id, "snapshot_ts": lk.snapshot_ts, "time_local": routine.local_time_str(h, lk.snapshot_ts),
                            "ring_event_id": lk.ring_event_id} for lk in looks],
            "expected_visits": [v.model_dump(mode="json") for v in visits],
            "text": text,
        }
