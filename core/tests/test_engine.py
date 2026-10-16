from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from core.engine import ladder, routine
from core.engine.clock import FakeClock
from core.engine.service import Engine, EngineError
from core.store import db
from core.store.models import Alert, AlertState, CheckInStatus, DecisionKind, Household

IST = ZoneInfo("Asia/Kolkata")
H = "home"
S = AlertState


def at(hhmm: str, day: int = 16) -> int:
    h, m = map(int, hhmm.split(":"))
    return int(datetime(2026, 10, day, h, m, tzinfo=IST).timestamp() * 1000)


@pytest.fixture
def env():
    conn = db.connect(":memory:")
    clock = FakeClock(at("07:00"))
    eng = Engine(conn, clock)
    eng.ensure_household(Household(id=H, parent_name="Dad", tz="Asia/Kolkata", checkin_by="10:00", grace_min=30,
                                   reprompt_min=10, secondary_after_min=20, monitoring_since=at("06:00")))
    yield eng, clock, conn
    conn.close()


def states(alert_view):
    return [t["to_state"] for t in alert_view["history"]]


def stamps(alert_view):
    return [t["time_local"] for t in alert_view["history"]]


# --- routine arithmetic ------------------------------------------------------

def test_deadline_is_checkin_by_plus_grace():
    h = Household(id="x", tz="Asia/Kolkata", checkin_by="10:00", grace_min=30)
    assert routine.deadline_ms(h, date(2026, 10, 16)) == at("10:30")


def test_day_bounds_are_dst_safe():
    h = Household(id="x", tz="America/New_York")
    start, end = routine.day_bounds(h, date(2026, 11, 1))  # DST ends: 25-hour day
    assert end - start == 25 * 3_600_000


def test_ladder_rejects_illegal_transition():
    with pytest.raises(ladder.InvalidTransition):
        ladder.transition(Alert(decision_id="d", state=S.RESOLVED), S.CHECKING_IN, 0)


# --- check-in rule -------------------------------------------------------------

def test_before_deadline_is_awaiting(env):
    eng, clock, _ = env
    st = eng.routine_status(H)
    assert st["routine_state"] == "awaiting"
    assert st["open_alerts"] == []
    assert "10:30" in st["text"]


def test_ok_checkin_prevents_alert(env):
    eng, clock, conn = env
    clock.t = at("08:05")
    eng.record_checkin(H, CheckInStatus.OK, "slept well")
    clock.t = at("12:00")
    st = eng.routine_status(H)
    assert st["routine_state"] == "checked_in"
    assert st["last_checkin"]["time_local"] == "08:05"
    assert db.list_alerts(conn, H) == []
    assert st["text"].startswith("Yes.")


def test_full_ladder_runs_on_schedule(env):
    eng, clock, _ = env
    clock.t = at("10:29")
    assert eng.routine_status(H)["open_alerts"] == []
    clock.t = at("10:30")
    a = eng.routine_status(H)["open_alerts"][0]
    assert a["kind"] == "missed_checkin" and a["state"] == "checking_in"
    clock.t = at("10:40")
    assert eng.routine_status(H)["open_alerts"][0]["state"] == "caregiver_alerted"
    clock.t = at("11:00")
    a = eng.routine_status(H)["open_alerts"][0]
    assert states(a) == ["checking_in", "caregiver_alerted", "secondary_alerted"]
    assert stamps(a) == ["10:30", "10:40", "11:00"]


def test_late_evaluation_reproduces_exact_audit_trail(env):
    eng, clock, _ = env
    clock.t = at("15:00")  # evaluator was down all morning
    st = eng.routine_status(H)
    a = st["open_alerts"][0]
    assert st["routine_state"] == "missed"
    assert states(a) == ["checking_in", "caregiver_alerted", "secondary_alerted"]
    assert stamps(a) == ["10:30", "10:40", "11:00"]  # due times, not 15:00


def test_evaluation_is_idempotent(env):
    eng, clock, conn = env
    clock.t = at("11:30")
    for _ in range(3):
        eng.evaluate(H)
    alerts = db.list_alerts(conn, H)
    assert len(alerts) == 1
    assert len(alerts[0][0].history) == 3


def test_no_answer_checkin_does_not_count_and_is_evidence(env):
    eng, clock, conn = env
    clock.t = at("09:00")
    c = eng.record_checkin(H, CheckInStatus.NO_ANSWER)
    clock.t = at("10:30")
    a = eng.routine_status(H)["open_alerts"][0]
    assert a["evidence"] == [c.id]


# --- answers and acknowledgements --------------------------------------------

def test_parent_answer_during_checking_in_resolves(env):
    eng, clock, conn = env
    clock.t = at("10:35")
    eng.record_checkin(H, CheckInStatus.OK)  # evaluates first, so the 10:30 alert exists
    (alert, decision), = db.list_alerts(conn, H)
    assert alert.state == S.RESOLVED
    assert [t.to_state for t in alert.history] == [S.CHECKING_IN, S.RESOLVED]
    assert alert.history[-1].actor == "parent"
    assert eng.routine_status(H)["routine_state"] == "checked_in"


def test_parent_answer_after_caregiver_alerted_resolves(env):
    eng, clock, conn = env
    clock.t = at("10:45")
    eng.record_checkin(H, CheckInStatus.OK)
    (alert, _), = db.list_alerts(conn, H)
    assert [t.to_state for t in alert.history] == [S.CHECKING_IN, S.CAREGIVER_ALERTED, S.RESOLVED]


def test_needs_help_without_alert_goes_straight_to_caregiver_then_escalates(env):
    eng, clock, conn = env
    clock.t = at("08:00")
    eng.record_checkin(H, CheckInStatus.NEEDS_HELP, "dizzy")
    a = eng.routine_status(H)["open_alerts"][0]
    assert a["kind"] == "help_requested" and a["state"] == "caregiver_alerted"
    assert "dizzy" in a["history"][0]["note"]
    clock.t = at("08:20")
    a = eng.routine_status(H)["open_alerts"][0]
    assert states(a) == ["caregiver_alerted", "secondary_alerted"]
    assert eng.routine_status(H)["routine_state"] == "needs_help"


def test_needs_help_while_checking_in_replaces_missed_alert(env):
    eng, clock, conn = env
    clock.t = at("10:32")
    eng.record_checkin(H, CheckInStatus.NEEDS_HELP)
    kinds = {d.kind: a.state for a, d in db.list_alerts(conn, H)}
    assert kinds == {DecisionKind.MISSED_CHECKIN: S.RESOLVED, DecisionKind.HELP_REQUESTED: S.CAREGIVER_ALERTED}


def test_repeated_needs_help_keeps_one_open_help_alert(env):
    eng, clock, conn = env
    eng.record_checkin(H, CheckInStatus.NEEDS_HELP)
    clock.advance(minutes=2)
    eng.record_checkin(H, CheckInStatus.NEEDS_HELP)
    assert len(db.list_alerts(conn, H, open_only=True)) == 1


def test_acknowledge_resolves_latest_open_alert(env):
    eng, clock, _ = env
    clock.t = at("10:50")
    alert = eng.acknowledge(H, None, "Asha", "Called him, he's fine")
    assert alert.state == S.RESOLVED
    assert alert.history[-1].actor == "Asha"
    assert eng.routine_status(H)["open_alerts"] == []
    # stays resolved: no new escalation later in the day
    clock.t = at("13:00")
    assert eng.routine_status(H)["open_alerts"] == []


def test_acknowledge_errors(env):
    eng, clock, _ = env
    with pytest.raises(EngineError, match="no open alert"):
        eng.acknowledge(H, None, "Asha")
    clock.t = at("10:30")
    alert = eng.acknowledge(H, None, "Asha")
    with pytest.raises(EngineError, match="already resolved"):
        eng.acknowledge(H, alert.id, "Asha")
    with pytest.raises(EngineError, match="unknown alert"):
        eng.acknowledge(H, "nope", "Asha")


# --- pause, monitoring window, next day --------------------------------------

def test_pause_suppresses_decision_and_resume_restores(env):
    eng, clock, conn = env
    eng.pause(H, 6, "Dad is visiting us")  # 07:00 -> 13:00 covers the 10:30 deadline
    clock.t = at("11:00")
    st = eng.routine_status(H)
    assert st["routine_state"] == "paused" and st["monitoring_paused"]
    assert db.list_alerts(conn, H) == []
    eng.pause(H, 0, "")
    clock.t = at("10:30", day=17)
    assert eng.routine_status(H)["open_alerts"][0]["kind"] == "missed_checkin"


def test_pause_bounds(env):
    eng, *_ = env
    with pytest.raises(EngineError):
        eng.pause(H, -1, "x")
    with pytest.raises(EngineError):
        eng.pause(H, 24 * 15, "x")


def test_deadline_before_monitoring_started_is_ignored(env):
    eng, clock, conn = env
    h = eng.household(H)
    h.monitoring_since = at("14:00")
    db.save_household(conn, h)
    clock.t = at("15:00")
    assert eng.routine_status(H)["routine_state"] == "not_monitored"
    clock.t = at("10:30", day=17)
    assert len(eng.routine_status(H)["open_alerts"]) == 1


def test_each_day_gets_its_own_decision(env):
    eng, clock, conn = env
    clock.t = at("10:30")
    eng.acknowledge(H, None, "Asha")
    clock.t = at("10:30", day=17)
    eng.evaluate(H)
    assert len(db.list_alerts(conn, H)) == 2


# --- visits and summary --------------------------------------------------------

def test_add_visit_validation(env):
    eng, *_ = env
    with pytest.raises(EngineError):
        eng.add_visit(H, "Carer", "11:00", "10:00", ["fri"])


def test_day_summary_lists_everything_for_the_day(env):
    eng, clock, _ = env
    eng.add_visit(H, "Carer", "11:00", "12:00", ["fri"])  # 16 Oct 2026 is a Friday
    eng.add_visit(H, "Physio", "15:00", "16:00", ["mon"])
    clock.t = at("08:10")
    eng.record_checkin(H, CheckInStatus.OK)
    s = eng.day_summary(H)
    assert s["date"] == "2026-10-16"
    assert [c["time_local"] for c in s["checkins"]] == ["08:10"]
    assert [v["label"] for v in s["expected_visits"]] == ["Carer"]
    assert "08:10 check-in: ok" in s["text"] and "Carer 11:00–12:00" in s["text"]
    assert eng.day_summary(H, date(2026, 10, 15))["text"].endswith("nothing recorded yet.")
