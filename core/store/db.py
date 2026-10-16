"""SQLite persistence for the shared contracts in models.py."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.store.models import (
    Alert,
    AlertState,
    AlertTransition,
    CheckIn,
    Decision,
    DecisionKind,
    DoorLook,
    Event,
    Household,
    Visit,
)

OPEN_STATES = ("ok", "checking_in", "caregiver_alerted", "secondary_alerted")

SCHEMA = """
CREATE TABLE IF NOT EXISTS households (
    id TEXT PRIMARY KEY,
    config TEXT NOT NULL  -- Household model as JSON
);

CREATE TABLE IF NOT EXISTS door_looks (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    requested_at INTEGER NOT NULL,
    snapshot_ts INTEGER NOT NULL,
    snapshot_path TEXT NOT NULL,
    ring_event_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_door_looks_household_ts ON door_looks (household_id, requested_at);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    device_id TEXT,
    type TEXT NOT NULL CHECK (type IN ('on_demand','checkin','visit_log')),
    ts_start INTEGER NOT NULL,
    ts_end INTEGER
);
CREATE INDEX IF NOT EXISTS ix_events_household_ts ON events (household_id, ts_start);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('missed_checkin','help_requested','visit_matched','visit_unexpected')),
    evidence TEXT NOT NULL DEFAULT '[]',
    ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL REFERENCES decisions (id),
    state TEXT NOT NULL CHECK (state IN ('ok','checking_in','caregiver_alerted','secondary_alerted','resolved'))
);

-- Append-only audit trail of escalation ladder steps.
CREATE TABLE IF NOT EXISTS alert_transitions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id TEXT NOT NULL REFERENCES alerts (id),
    from_state TEXT,
    to_state TEXT NOT NULL,
    ts INTEGER NOT NULL,
    actor TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS visits (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    label TEXT NOT NULL,
    start TEXT NOT NULL,
    "end" TEXT NOT NULL,
    days TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS checkins (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok','needs_help','no_answer')),
    note TEXT NOT NULL DEFAULT '',
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_checkins_household_ts ON checkins (household_id, ts);
"""


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# --- households -----------------------------------------------------------

def save_household(conn: sqlite3.Connection, h: Household) -> None:
    conn.execute("INSERT OR REPLACE INTO households (id, config) VALUES (?, ?)", (h.id, h.model_dump_json()))
    conn.commit()


def get_household(conn: sqlite3.Connection, household_id: str) -> Household | None:
    r = conn.execute("SELECT config FROM households WHERE id = ?", (household_id,)).fetchone()
    return Household.model_validate_json(r["config"]) if r else None


# --- door looks -----------------------------------------------------------

def insert_door_look(conn: sqlite3.Connection, d: DoorLook) -> None:
    conn.execute(
        "INSERT INTO door_looks (id, household_id, device_id, requested_at, snapshot_ts, snapshot_path, ring_event_id)"
        " VALUES (?,?,?,?,?,?,?)",
        (d.id, d.household_id, d.device_id, d.requested_at, d.snapshot_ts, d.snapshot_path, d.ring_event_id),
    )
    conn.commit()


def set_door_look_event(conn: sqlite3.Connection, look_id: str, ring_event_id: str) -> None:
    conn.execute("UPDATE door_looks SET ring_event_id = ? WHERE id = ?", (ring_event_id, look_id))
    conn.commit()


def get_door_look(conn: sqlite3.Connection, look_id: str) -> DoorLook | None:
    r = conn.execute("SELECT * FROM door_looks WHERE id = ?", (look_id,)).fetchone()
    return DoorLook(**dict(r)) if r else None


def list_door_looks(conn: sqlite3.Connection, household_id: str, since_ms: int = 0,
                    until_ms: int | None = None) -> list[DoorLook]:
    q, args = _range("SELECT * FROM door_looks WHERE household_id = ?", [household_id], "requested_at", since_ms, until_ms)
    return [DoorLook(**dict(r)) for r in conn.execute(q + " ORDER BY requested_at", args).fetchall()]


def _range(q: str, args: list, col: str, since_ms: int, until_ms: int | None) -> tuple[str, list]:
    q += f" AND {col} >= ?"
    args = [*args, since_ms]
    if until_ms is not None:
        q += f" AND {col} < ?"
        args.append(until_ms)
    return q, args


# --- events ---------------------------------------------------------------

def upsert_event(conn: sqlite3.Connection, e: Event) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO events (id, household_id, device_id, type, ts_start, ts_end) VALUES (?,?,?,?,?,?)",
        (e.id, e.household_id, e.device_id, e.type.value, e.ts_start, e.ts_end),
    )
    conn.commit()


def list_events(conn: sqlite3.Connection, household_id: str, since_ms: int = 0) -> list[Event]:
    rows = conn.execute(
        "SELECT * FROM events WHERE household_id = ? AND ts_start >= ? ORDER BY ts_start",
        (household_id, since_ms),
    ).fetchall()
    return [Event(**dict(r)) for r in rows]


# --- decisions ------------------------------------------------------------

def insert_decision(conn: sqlite3.Connection, d: Decision) -> None:
    conn.execute(
        "INSERT INTO decisions (id, household_id, kind, evidence, ts) VALUES (?,?,?,?,?)",
        (d.id, d.household_id, d.kind.value, json.dumps(d.evidence), d.ts),
    )
    conn.commit()


def _decision(r: sqlite3.Row) -> Decision:
    return Decision(**{**dict(r), "evidence": json.loads(r["evidence"])})


def get_decision(conn: sqlite3.Connection, decision_id: str) -> Decision | None:
    r = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return _decision(r) if r else None


def find_decision(conn: sqlite3.Connection, household_id: str, kind: DecisionKind, ts: int) -> Decision | None:
    """Exact lookup used to keep rule evaluation idempotent (decision ts = the moment it became due)."""
    r = conn.execute("SELECT * FROM decisions WHERE household_id = ? AND kind = ? AND ts = ?",
                     (household_id, kind.value, ts)).fetchone()
    return _decision(r) if r else None


# --- alerts ---------------------------------------------------------------

def insert_alert(conn: sqlite3.Connection, a: Alert) -> None:
    conn.execute("INSERT INTO alerts (id, decision_id, state) VALUES (?,?,?)", (a.id, a.decision_id, a.state.value))
    for t in a.history:
        _insert_transition(conn, a.id, t)
    conn.commit()


def record_transition(conn: sqlite3.Connection, alert_id: str, t: AlertTransition) -> None:
    """Move an alert to t.to_state and append the step to the audit trail, atomically."""
    with conn:
        conn.execute("UPDATE alerts SET state = ? WHERE id = ?", (t.to_state.value, alert_id))
        _insert_transition(conn, alert_id, t)


def _insert_transition(conn: sqlite3.Connection, alert_id: str, t: AlertTransition) -> None:
    conn.execute(
        "INSERT INTO alert_transitions (alert_id, from_state, to_state, ts, actor, note) VALUES (?,?,?,?,?,?)",
        (alert_id, t.from_state.value if t.from_state else None, t.to_state.value, t.ts, t.actor, t.note),
    )


def get_alert(conn: sqlite3.Connection, alert_id: str) -> Alert | None:
    r = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if r is None:
        return None
    hist = conn.execute(
        "SELECT from_state, to_state, ts, actor, note FROM alert_transitions WHERE alert_id = ? ORDER BY seq",
        (alert_id,),
    ).fetchall()
    return Alert(id=r["id"], decision_id=r["decision_id"], state=AlertState(r["state"]),
                 history=[AlertTransition(**dict(h)) for h in hist])


def list_alerts(conn: sqlite3.Connection, household_id: str, *, open_only: bool = False,
                since_ms: int = 0, until_ms: int | None = None) -> list[tuple[Alert, Decision]]:
    """Alerts with their decisions, oldest decision first."""
    q = ("SELECT a.id AS alert_id, d.* FROM alerts a JOIN decisions d ON d.id = a.decision_id"
         " WHERE d.household_id = ?")
    q, args = _range(q, [household_id], "d.ts", since_ms, until_ms)
    if open_only:
        q += f" AND a.state IN ({','.join('?' * len(OPEN_STATES))})"
        args += list(OPEN_STATES)
    rows = conn.execute(q + " ORDER BY d.ts", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        alert_id = d.pop("alert_id")
        out.append((get_alert(conn, alert_id), Decision(**{**d, "evidence": json.loads(d["evidence"])})))
    return out


# --- visits ---------------------------------------------------------------

def insert_visit(conn: sqlite3.Connection, v: Visit) -> None:
    conn.execute(
        'INSERT INTO visits (id, household_id, label, start, "end", days) VALUES (?,?,?,?,?,?)',
        (v.id, v.household_id, v.label, v.start, v.end, json.dumps([d.value for d in v.days])),
    )
    conn.commit()


def list_visits(conn: sqlite3.Connection, household_id: str) -> list[Visit]:
    rows = conn.execute("SELECT * FROM visits WHERE household_id = ? ORDER BY start", (household_id,)).fetchall()
    return [Visit(**{**dict(r), "days": json.loads(r["days"])}) for r in rows]


# --- check-ins ------------------------------------------------------------

def insert_checkin(conn: sqlite3.Connection, c: CheckIn) -> None:
    conn.execute(
        "INSERT INTO checkins (id, household_id, status, note, ts) VALUES (?,?,?,?,?)",
        (c.id, c.household_id, c.status.value, c.note, c.ts),
    )
    conn.commit()


def list_checkins(conn: sqlite3.Connection, household_id: str, since_ms: int = 0, until_ms: int | None = None) -> list[CheckIn]:
    q, args = _range("SELECT * FROM checkins WHERE household_id = ?", [household_id], "ts", since_ms, until_ms)
    rows = conn.execute(q + " ORDER BY ts", args).fetchall()
    return [CheckIn(**dict(r)) for r in rows]
