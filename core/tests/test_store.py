import pytest
from pydantic import ValidationError

from core.store import db
from core.store.models import (
    Alert,
    AlertState,
    AlertTransition,
    CheckIn,
    CheckInStatus,
    Decision,
    DecisionKind,
    Event,
    EventType,
    Visit,
)

H = "household-1"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_event_roundtrip_and_upsert(conn):
    e = Event(id="ring-evt-1", household_id=H, device_id="dev", type=EventType.ON_DEMAND, ts_start=1000, ts_end=41000)
    db.upsert_event(conn, e)
    db.upsert_event(conn, e)  # same Ring event seen twice -> one row
    assert db.list_events(conn, H) == [e]
    assert db.list_events(conn, H, since_ms=2000) == []


def test_decision_roundtrip_keeps_evidence(conn):
    d = Decision(household_id=H, kind=DecisionKind.MISSED_CHECKIN, evidence=["e1", "e2"], ts=5)
    db.insert_decision(conn, d)
    assert db.get_decision(conn, d.id) == d


def test_alert_transitions_are_audited_in_order(conn):
    d = Decision(household_id=H, kind=DecisionKind.MISSED_CHECKIN, ts=1)
    db.insert_decision(conn, d)
    a = Alert(decision_id=d.id, state=AlertState.CHECKING_IN,
              history=[AlertTransition(from_state=AlertState.OK, to_state=AlertState.CHECKING_IN, ts=10)])
    db.insert_alert(conn, a)
    db.record_transition(conn, a.id, AlertTransition(
        from_state=AlertState.CHECKING_IN, to_state=AlertState.CAREGIVER_ALERTED, ts=20))
    db.record_transition(conn, a.id, AlertTransition(
        from_state=AlertState.CAREGIVER_ALERTED, to_state=AlertState.RESOLVED, ts=30, actor="caregiver", note="Dad called"))

    got = db.get_alert(conn, a.id)
    assert got.state == AlertState.RESOLVED
    assert [t.to_state for t in got.history] == [AlertState.CHECKING_IN, AlertState.CAREGIVER_ALERTED, AlertState.RESOLVED]
    assert got.history[-1].actor == "caregiver"


def test_alert_requires_existing_decision(conn):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_alert(conn, Alert(decision_id="missing"))


def test_visit_roundtrip_and_time_validation(conn):
    v = Visit(household_id=H, label="Carer", start="9:30", end="10:30", days=["tue", "fri"])
    assert v.start == "09:30"
    db.insert_visit(conn, v)
    assert db.list_visits(conn, H) == [v]
    with pytest.raises(ValidationError):
        Visit(household_id=H, label="bad", start="25:00", end="10:00")
    with pytest.raises(ValidationError):
        Visit(household_id=H, label="bad", start="10:00", end="11:00", days=["funday"])


def test_checkins_window_query(conn):
    for ts, status in [(100, CheckInStatus.NO_ANSWER), (200, CheckInStatus.OK), (300, CheckInStatus.NEEDS_HELP)]:
        db.insert_checkin(conn, CheckIn(household_id=H, status=status, ts=ts))
    got = db.list_checkins(conn, H, since_ms=150, until_ms=300)
    assert [c.status for c in got] == [CheckInStatus.OK]


def test_db_rejects_invalid_enum_values(conn):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO checkins (id, household_id, status, ts) VALUES ('x', ?, 'maybe', 1)", (H,))
