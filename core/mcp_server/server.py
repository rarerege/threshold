"""Threshold MCP server (MCP spec 2025-11-25, Streamable HTTP at /mcp).

Rules decide; tools only read state, record facts, or run the Ring "look at the door".
Every tool returns a short text block plus structuredContent matching its outputSchema.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable, Literal

from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response

from core.engine import routine
from core.engine.service import Engine, EngineError
from core.mcp_server import schemas as out
from core.ring.client import RingAuthError, RingClient, RingError, RingMediaNotFound
from core.ring.live_view import look_at_door, wait_for_event
from core.store.models import CheckInStatus, DoorLook, new_id
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations

log = logging.getLogger(__name__)

SNAPSHOT_NAME = re.compile(r"^[0-9a-f]{32}\.jpg$")
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

INSTRUCTIONS = """Threshold helps an adult child keep an eye on a parent who lives alone.
Alerts are decided by deterministic rules; tools report state and never guess.
Never claim to recognise who is at the door, and never call emergency services."""


def _result(model: Any, extra: list | None = None) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=model.text), *(extra or [])],
                          structured_content=model.model_dump(mode="json"))


def _error(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def build_server(
    engine: Engine,
    household_id: str,
    *,
    snapshot_dir: Path,
    public_base_url: str = "http://127.0.0.1:8000",
    ring_factory: Callable[[], RingClient] = RingClient.from_env,
    look: Callable[..., Awaitable[Any]] = look_at_door,
    find_event: Callable[..., Awaitable[dict]] = wait_for_event,
) -> MCPServer:
    mcp = MCPServer(name="threshold", title="Threshold", version="0.1.0", instructions=INSTRUCTIONS)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    door_lock = asyncio.Lock()
    background: set[asyncio.Task] = set()
    device_cache: dict[str, str] = {}

    def snapshot_url(look_id: str) -> str:
        return f"{public_base_url}/snapshots/{look_id}.jpg"

    # --- read tools ----------------------------------------------------------

    @mcp.tool(
        title="Routine status",
        description="Did the parent check in today? Returns today's check-in state, the deadline, "
                    "and any open alerts with their full escalation history.",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    async def get_routine_status() -> Annotated[CallToolResult, out.RoutineStatus]:
        return _result(out.RoutineStatus(**engine.routine_status(household_id)))

    @mcp.tool(
        title="Day summary",
        description="Everything recorded for one day: check-ins, alerts (with history), door snapshots "
                    "and expected visits. Defaults to today in the parent's time zone.",
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
    )
    async def get_day_summary(
        date_iso: Annotated[str | None, Field(description="Day as YYYY-MM-DD; omit for today",
                                              pattern=r"^\d{4}-\d{2}-\d{2}$")] = None,
    ) -> Annotated[CallToolResult, out.DaySummary]:
        try:
            day = date.fromisoformat(date_iso) if date_iso else None
        except ValueError:
            return _error(f"Invalid date {date_iso!r}; use YYYY-MM-DD.")
        s = engine.day_summary(household_id, day)
        for lk in s["door_looks"]:
            lk["snapshot_url"] = snapshot_url(lk["id"])
        return _result(out.DaySummary(**s))

    # --- write tools ---------------------------------------------------------

    @mcp.tool(
        title="Record check-in",
        description="Record the parent's answer to the daily check-in ('Good morning, how are you today?'). "
                    "ok resolves a pending missed-check-in alert; needs_help alerts the caregiver immediately; "
                    "no_answer records an unanswered prompt and does not count as a check-in.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                    open_world_hint=False),
    )
    async def record_checkin(
        status: Annotated[Literal["ok", "needs_help", "no_answer"], Field(description="The parent's answer")],
        note: Annotated[str, Field(description="Optional words from the parent", max_length=500)] = "",
    ) -> Annotated[CallToolResult, out.CheckInResult]:
        c = engine.record_checkin(household_id, CheckInStatus(status), note)
        st = engine.routine_status(household_id)
        h = engine.household(household_id)
        text = {
            "ok": f"Thanks, recorded {h.parent_name}'s check-in at {routine.local_time_str(h, c.ts)}.",
            "needs_help": f"Recorded that {h.parent_name} needs help; the caregiver has been alerted.",
            "no_answer": "Recorded an unanswered check-in prompt.",
        }[status]
        return _result(out.CheckInResult(checkin_id=c.id, status=status, ts=c.ts,
                                         time_local=routine.local_time_str(h, c.ts),
                                         routine_state=st["routine_state"], open_alerts=st["open_alerts"], text=text))

    @mcp.tool(
        title="Add expected visit",
        description="Tell Threshold about a recurring expected visit (e.g. a carer on Tue/Fri 10:00-11:00). "
                    "Times are local to the parent's home.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
    )
    async def add_expected_visit(
        label: Annotated[str, Field(description="Who or what, e.g. 'Carer' or 'Physio'", min_length=1, max_length=80)],
        start: Annotated[str, Field(description="Local start time HH:MM", pattern=r"^\d{1,2}:\d{2}$")],
        end: Annotated[str, Field(description="Local end time HH:MM", pattern=r"^\d{1,2}:\d{2}$")],
        days: Annotated[list[Weekday], Field(description="Days of the week", min_length=1)],
    ) -> Annotated[CallToolResult, out.VisitResult]:
        try:
            v = engine.add_visit(household_id, label, start, end, days)
        except (EngineError, ValueError) as e:
            return _error(str(e))
        text = f"Added: {v.label}, {', '.join(d.value.title() for d in v.days)} {v.start}–{v.end}."
        return _result(out.VisitResult(visit=v.model_dump(mode="json"), text=text))

    @mcp.tool(
        title="Acknowledge alert",
        description="Caregiver acknowledges an alert, which stops further escalation and marks it resolved. "
                    "Omit alert_id to acknowledge the most recent open alert.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                    open_world_hint=False),
    )
    async def acknowledge_alert(
        alert_id: Annotated[str | None, Field(description="Alert id from get_routine_status")] = None,
        acknowledged_by: Annotated[str, Field(description="Who is acknowledging", max_length=80)] = "caregiver",
        note: Annotated[str, Field(description="Optional note, e.g. 'Called him, he's fine'", max_length=500)] = "",
    ) -> Annotated[CallToolResult, out.AckResult]:
        try:
            a = engine.acknowledge(household_id, alert_id, acknowledged_by, note)
        except EngineError as e:
            return _error(str(e))
        return _result(out.AckResult(alert_id=a.id, state=a.state.value, acknowledged_by=acknowledged_by,
                                     history=engine.alert_view(a)["history"],
                                     text=f"Alert acknowledged by {acknowledged_by} and resolved."))

    @mcp.tool(
        title="Pause monitoring",
        description="WRITE ACTION THAT REQUIRES EXPLICIT USER CONFIRMATION. Pauses missed-check-in alerts for a "
                    "number of hours (e.g. the parent is travelling); hours=0 resumes monitoring. Ask the user to "
                    "confirm the duration first, then call again with confirm=true. Without confirm=true nothing "
                    "changes. Existing open alerts keep escalating until acknowledged.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True,
                                    open_world_hint=False),
    )
    async def pause_monitoring(
        hours: Annotated[float, Field(description="How long to pause, 0 to resume", ge=0, le=336)],
        reason: Annotated[str, Field(description="Why, e.g. 'Dad is staying with us'", max_length=200)] = "",
        confirm: Annotated[bool, Field(description="Must be true, and only after the user explicitly confirmed")] = False,
    ) -> Annotated[CallToolResult, out.PauseResult]:
        h = engine.household(household_id)
        if not confirm:
            what = "resume monitoring" if hours == 0 else f"pause check-in alerts for {hours:g} hours"
            return _result(out.PauseResult(status="needs_confirmation", paused_until=None, paused_until_local=None,
                                           reason=reason, text=f"Please confirm: {what}? Nothing has changed yet."))
        try:
            h = engine.pause(household_id, hours, reason)
        except EngineError as e:
            return _error(str(e))
        if h.paused_until is None:
            return _result(out.PauseResult(status="resumed", paused_until=None, paused_until_local=None, reason="",
                                           text="Monitoring resumed."))
        until_local = routine.local_time_str(h, h.paused_until)
        day = routine.local_date(h, h.paused_until).isoformat()
        return _result(out.PauseResult(status="paused", paused_until=h.paused_until,
                                       paused_until_local=f"{day} {until_local}", reason=reason,
                                       text=f"Check-in alerts paused until {day} {until_local}."))

    # --- Ring ----------------------------------------------------------------

    @mcp.tool(
        title="Check the door",
        description="Look at the front door now: starts a short Ring live view (which creates an on_demand "
                    "event), takes a snapshot from inside that event, and returns the image and timestamps. "
                    "Takes about 10 seconds. Does not identify people.",
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False,
                                    open_world_hint=True),
    )
    async def check_the_door() -> Annotated[CallToolResult, out.DoorResult]:
        if door_lock.locked():
            return _error("A door check is already in progress; try again in a few seconds.")
        async with door_lock:
            try:
                async with ring_factory() as ring:
                    if "id" not in device_cache:
                        devices = await ring.list_devices()
                        if not devices:
                            return _error("No Ring devices are linked to this account.")
                        dev = next((d for d in devices if d["attributes"].get("name") == "Playground Device"), devices[0])
                        device_cache.update(id=dev["id"], name=dev["attributes"].get("name", "Ring device"))
                    result = await look(ring, device_cache["id"])
            except RingAuthError:
                return _error("The Ring token has expired. Generate a new one in the Ring Developer Playground "
                              "and paste it into .env as RING_ACCESS_TOKEN.")
            except RingMediaNotFound:
                return _error("Ring started the live view but had no image for that moment. Please try again.")
            except RingError as e:
                return _error(f"Ring didn't respond as expected ({e.status or 'network'}). Please try again.")

        look_id = new_id()
        path = snapshot_dir / f"{look_id}.jpg"
        path.write_bytes(result.snapshot)
        engine.record_door_look(DoorLook(id=look_id, household_id=household_id, device_id=device_cache["id"],
                                         requested_at=result.requested_at, snapshot_ts=result.snapshot_ts,
                                         snapshot_path=str(path)))
        task = asyncio.create_task(_attach_event(look_id, result))
        background.add(task)
        task.add_done_callback(background.discard)

        h = engine.household(household_id)
        when = routine.local_time_str(h, result.snapshot_ts)
        model = out.DoorResult(
            look_id=look_id, device_name=device_cache["name"], snapshot_url=snapshot_url(look_id),
            snapshot_ts=result.snapshot_ts, snapshot_time_local=when, requested_at=result.requested_at,
            ring_event_id=None, event_status="pending",
            text=f"Here's the door at {when}, from {device_cache['name']}. I can't tell who anyone is, only show the picture.",
        )
        image = ImageContent(type="image", data=base64.b64encode(result.snapshot).decode(), mime_type="image/jpeg")
        return _result(model, [image])

    async def _attach_event(look_id: str, result: Any) -> None:
        """Ring lists the on_demand event only after its ~40 s window; link it as evidence when it appears."""
        try:
            async with ring_factory() as ring:
                event = await find_event(ring, result)
            engine.attach_door_event(look_id, event)
            log.info("door look %s matched Ring event %s", look_id, event["id"][:8])
        except Exception as e:  # background best-effort; the snapshot is already stored
            log.warning("door look %s: could not match Ring event: %s", look_id, e)

    @mcp.custom_route("/snapshots/{name}", methods=["GET"])
    async def get_snapshot(request: Request) -> Response:
        name = request.path_params["name"]
        if not SNAPSHOT_NAME.match(name) or not (snapshot_dir / name).is_file():
            return Response(status_code=404)
        return FileResponse(snapshot_dir / name, media_type="image/jpeg")

    return mcp


def build_app(mcp: MCPServer, engine: Engine, household_id: str, *, tick_s: float = 30.0) -> Starlette:
    """Streamable HTTP app at /mcp plus a background timer that applies due ladder steps."""
    app = mcp.streamable_http_app(streamable_http_path="/mcp")
    inner = app.router.lifespan_context

    async def tick() -> None:
        while True:
            try:
                engine.evaluate(household_id)
            except Exception:
                log.exception("evaluation failed")
            await asyncio.sleep(tick_s)

    @asynccontextmanager
    async def lifespan(a: Starlette):
        async with inner(a) as state:
            task = asyncio.create_task(tick())
            try:
                yield state
            finally:
                task.cancel()

    app.router.lifespan_context = lifespan
    return app
