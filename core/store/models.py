"""Shared contracts between Ring client, engine, MCP server and web client.

Timestamps are epoch milliseconds (UTC), matching the Ring API. Visit start/end are
local wall-clock times ("HH:MM") because they describe a recurring routine.
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


def new_id() -> str:
    return uuid.uuid4().hex


def now_ms() -> int:
    return int(time.time() * 1000)


class EventType(StrEnum):
    ON_DEMAND = "on_demand"
    CHECKIN = "checkin"
    VISIT_LOG = "visit_log"


class DecisionKind(StrEnum):
    MISSED_CHECKIN = "missed_checkin"
    VISIT_MATCHED = "visit_matched"
    VISIT_UNEXPECTED = "visit_unexpected"


class AlertState(StrEnum):
    OK = "ok"
    CHECKING_IN = "checking_in"
    CAREGIVER_ALERTED = "caregiver_alerted"
    SECONDARY_ALERTED = "secondary_alerted"
    RESOLVED = "resolved"


class CheckInStatus(StrEnum):
    OK = "ok"
    NEEDS_HELP = "needs_help"
    NO_ANSWER = "no_answer"


class Weekday(StrEnum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


class Event(BaseModel):
    """Something that happened at the household. For Ring events, id is the Ring event id."""

    id: str = Field(default_factory=new_id)
    household_id: str
    device_id: str | None = None
    type: EventType
    ts_start: int
    ts_end: int | None = None


class Decision(BaseModel):
    """Output of a deterministic rule, pointing at the events that justify it."""

    id: str = Field(default_factory=new_id)
    household_id: str
    kind: DecisionKind
    evidence: list[str] = Field(default_factory=list)  # Event ids
    ts: int = Field(default_factory=now_ms)


class AlertTransition(BaseModel):
    """One audited step on the escalation ladder."""

    from_state: AlertState | None
    to_state: AlertState
    ts: int = Field(default_factory=now_ms)
    actor: str = "engine"  # "engine" or who acknowledged
    note: str = ""


class Alert(BaseModel):
    id: str = Field(default_factory=new_id)
    decision_id: str
    state: AlertState = AlertState.OK
    history: list[AlertTransition] = Field(default_factory=list)


class Visit(BaseModel):
    """An expected recurring visit, e.g. a carer every Tue/Fri 10:00-11:00."""

    id: str = Field(default_factory=new_id)
    household_id: str
    label: str
    start: str  # "HH:MM" local
    end: str  # "HH:MM" local
    days: list[Weekday] = Field(default_factory=list)

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        h, _, m = v.partition(":")
        if not (h.isdigit() and m.isdigit() and len(m) == 2 and 0 <= int(h) < 24 and 0 <= int(m) < 60):
            raise ValueError(f"expected HH:MM, got {v!r}")
        return f"{int(h):02d}:{m}"


class CheckIn(BaseModel):
    id: str = Field(default_factory=new_id)
    household_id: str
    status: CheckInStatus
    note: str = ""
    ts: int = Field(default_factory=now_ms)
