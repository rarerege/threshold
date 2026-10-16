"""Structured outputs (MCP outputSchema) for every Threshold tool."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AlertStep(BaseModel):
    from_state: str | None
    to_state: str
    ts: int
    time_local: str
    actor: str
    note: str


class AlertView(BaseModel):
    alert_id: str
    kind: str = Field(description="missed_checkin | help_requested | visit_matched | visit_unexpected")
    state: str = Field(description="ok | checking_in | caregiver_alerted | secondary_alerted | resolved")
    opened_at: int
    opened_at_local: str
    evidence: list[str] = Field(description="Event ids that justify the decision behind this alert")
    history: list[AlertStep]


class LastCheckIn(BaseModel):
    status: str
    ts: int
    time_local: str
    note: str


class RoutineStatus(BaseModel):
    household_id: str
    parent_name: str
    date: str
    now: int
    now_local: str
    checkin_by: str
    deadline: int
    deadline_local: str
    routine_state: Literal["checked_in", "needs_help", "awaiting", "missed", "paused", "not_monitored"]
    checked_in_today: bool
    last_checkin: LastCheckIn | None
    open_alerts: list[AlertView]
    monitoring_paused: bool
    paused_until: int | None
    pause_reason: str
    text: str


class CheckInResult(BaseModel):
    checkin_id: str
    status: str
    ts: int
    time_local: str
    routine_state: str
    open_alerts: list[AlertView]
    text: str


class CheckInItem(BaseModel):
    id: str
    status: str
    ts: int
    time_local: str
    note: str


class DoorLookItem(BaseModel):
    id: str
    snapshot_ts: int
    time_local: str
    ring_event_id: str | None
    snapshot_url: str


class VisitItem(BaseModel):
    id: str
    household_id: str
    label: str
    start: str
    end: str
    days: list[str]


class DaySummary(BaseModel):
    household_id: str
    date: str
    checkins: list[CheckInItem]
    alerts: list[AlertView]
    door_looks: list[DoorLookItem]
    expected_visits: list[VisitItem]
    text: str


class VisitResult(BaseModel):
    visit: VisitItem
    text: str


class AckResult(BaseModel):
    alert_id: str
    state: str
    acknowledged_by: str
    history: list[AlertStep]
    text: str


class DoorResult(BaseModel):
    look_id: str
    device_name: str
    snapshot_url: str = Field(description="HTTP URL of the JPEG served by this MCP server")
    snapshot_ts: int = Field(description="Epoch ms of the frame (inside the on_demand event)")
    snapshot_time_local: str
    requested_at: int = Field(description="Epoch ms when the live view was requested")
    ring_event_id: str | None = Field(description="Ring on_demand event id; null until Ring lists it (~45 s)")
    event_status: Literal["pending", "matched"]
    text: str


class PauseResult(BaseModel):
    status: Literal["needs_confirmation", "paused", "resumed"]
    paused_until: int | None
    paused_until_local: str | None
    reason: str
    text: str
