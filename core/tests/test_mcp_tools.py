"""MCP tool layer: schemas, structured output, confirmation gate, Ring error mapping (Ring faked)."""

import asyncio
import base64
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.engine.clock import FakeClock
from core.engine.service import Engine
from core.mcp_server.server import build_server
from core.ring.client import RingAuthError
from core.ring.live_view import DoorLook as RingLook
from core.store import db
from core.store.models import Household

IST = ZoneInfo("Asia/Kolkata")
JPEG = b"\xff\xd8\xff" + b"x" * 100


def at(hhmm, day=16):
    h, m = map(int, hhmm.split(":"))
    return int(datetime(2026, 10, day, h, m, tzinfo=IST).timestamp() * 1000)


class FakeRing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def list_devices(self):
        return [{"id": "DEV", "attributes": {"name": "Playground Device"}}]


@pytest.fixture
def setup(tmp_path):
    conn = db.connect(":memory:")
    clock = FakeClock(at("07:00"))
    eng = Engine(conn, clock)
    eng.ensure_household(Household(id="home", monitoring_since=at("06:00")))
    state = {"look_error": None, "event": {"id": "RINGEVT", "attributes": {"start": at("07:00") + 2300, "end": at("07:00") + 42000}}}

    async def fake_look(ring, device_id):
        if state["look_error"]:
            raise state["look_error"]
        return RingLook(device_id, JPEG, clock.now_ms() + 3000, clock.now_ms())

    async def fake_find(ring, look):
        return state["event"]

    mcp = build_server(eng, "home", snapshot_dir=tmp_path, ring_factory=FakeRing, look=fake_look, find_event=fake_find)
    return mcp, eng, clock, state, tmp_path


async def call(mcp, name, args=None):
    r = await mcp.call_tool(name, args or {})
    return r


async def test_tools_list_has_schemas_and_annotations(setup):
    mcp, *_ = setup
    tools = {t.name: t for t in await mcp.list_tools()}
    assert set(tools) == {"record_checkin", "get_routine_status", "get_day_summary", "add_expected_visit",
                          "acknowledge_alert", "check_the_door", "pause_monitoring"}
    for t in tools.values():
        assert t.input_schema["type"] == "object"
        assert t.output_schema and t.output_schema["type"] == "object"
    assert tools["record_checkin"].input_schema["properties"]["status"]["enum"] == ["ok", "needs_help", "no_answer"]
    assert "EXPLICIT USER CONFIRMATION" in tools["pause_monitoring"].description
    assert tools["get_routine_status"].annotations.read_only_hint is True
    assert tools["pause_monitoring"].annotations.destructive_hint is True


async def test_checkin_then_status(setup):
    mcp, eng, clock, *_ = setup
    clock.t = at("08:15")
    r = await call(mcp, "record_checkin", {"status": "ok", "note": "good"})
    assert not r.is_error and r.structured_content["routine_state"] == "checked_in"
    r = await call(mcp, "get_routine_status")
    assert r.structured_content["checked_in_today"] is True
    assert r.content[0].text.startswith("Yes. Dad checked in at 08:15")


async def test_missed_checkin_then_acknowledge(setup):
    mcp, eng, clock, *_ = setup
    clock.t = at("10:45")
    st = (await call(mcp, "get_routine_status")).structured_content
    assert st["routine_state"] == "missed"
    assert st["open_alerts"][0]["state"] == "caregiver_alerted"
    r = await call(mcp, "acknowledge_alert", {"acknowledged_by": "Asha", "note": "called"})
    assert r.structured_content["state"] == "resolved"
    assert [s["to_state"] for s in r.structured_content["history"]] == ["checking_in", "caregiver_alerted", "resolved"]
    r = await call(mcp, "acknowledge_alert")
    assert r.is_error and "no open alert" in r.content[0].text


async def test_pause_requires_confirmation(setup):
    mcp, eng, *_ = setup
    r = await call(mcp, "pause_monitoring", {"hours": 4, "reason": "trip"})
    assert r.structured_content["status"] == "needs_confirmation"
    assert eng.household("home").paused_until is None
    r = await call(mcp, "pause_monitoring", {"hours": 4, "reason": "trip", "confirm": True})
    assert r.structured_content["status"] == "paused"
    assert eng.household("home").paused_until is not None
    r = await call(mcp, "pause_monitoring", {"hours": 0, "confirm": True})
    assert r.structured_content["status"] == "resumed"


async def test_add_visit_and_summary(setup):
    mcp, *_ = setup
    r = await call(mcp, "add_expected_visit", {"label": "Carer", "start": "11:00", "end": "12:00", "days": ["fri"]})
    assert r.structured_content["visit"]["days"] == ["fri"]
    r = await call(mcp, "add_expected_visit", {"label": "Bad", "start": "12:00", "end": "11:00", "days": ["fri"]})
    assert r.is_error
    s = (await call(mcp, "get_day_summary")).structured_content
    assert [v["label"] for v in s["expected_visits"]] == ["Carer"]
    r = await call(mcp, "get_day_summary", {"date_iso": "2026-02-30"})
    assert r.is_error


async def test_invalid_arguments_are_rejected(setup):
    mcp, *_ = setup
    from mcp.server.mcpserver.exceptions import ToolError  # surfaced to clients as isError results
    with pytest.raises(ToolError, match="Input should be 'ok', 'needs_help' or 'no_answer'"):
        await call(mcp, "record_checkin", {"status": "fine"})


async def test_check_the_door_returns_image_and_links_event(setup):
    mcp, eng, clock, state, snapdir = setup
    r = await call(mcp, "check_the_door")
    assert not r.is_error
    sc = r.structured_content
    assert sc["event_status"] == "pending" and sc["ring_event_id"] is None
    assert sc["snapshot_url"].endswith(f"/snapshots/{sc['look_id']}.jpg")
    assert "can't tell who" in r.content[0].text
    img = r.content[1]
    assert img.type == "image" and img.mime_type == "image/jpeg"
    assert base64.b64decode(img.data) == JPEG
    assert (snapdir / f"{sc['look_id']}.jpg").read_bytes() == JPEG
    await asyncio.sleep(0)  # let the background matcher run
    await asyncio.sleep(0)
    s = (await call(mcp, "get_day_summary")).structured_content
    assert s["door_looks"][0]["ring_event_id"] == "RINGEVT"
    assert db.list_events(eng.conn, "home")[0].id == "RINGEVT"


async def test_check_the_door_expired_token(setup):
    mcp, _, _, state, _ = setup
    state["look_error"] = RingAuthError("expired", status=401)
    r = await call(mcp, "check_the_door")
    assert r.is_error and "token has expired" in r.content[0].text
