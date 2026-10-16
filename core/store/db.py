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
    Event,
    Visit,
)

SCHEMA = """
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
    kind TEXT NOT NULL CHECK (kind IN ('missed_checkin','visit_matched','visit_unexpected')),
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


def get_decision(conn: sqlite3.Connection, decision_id: str) -> Decision | None:
    r = conn.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    if r is None:
        return None
    return Decision(**{**dict(r), "evidence": json.loads(r["evidence"])})


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
    q = "SELECT * FROM checkins WHERE household_id = ? AND ts >= ?"
    args: list = [household_id, since_ms]
    if until_ms is not None:
        q += " AND ts < ?"
        args.append(until_ms)
    rows = conn.execute(q + " ORDER BY ts", args).fetchall()
    return [CheckIn(**dict(r)) for r in rows]
