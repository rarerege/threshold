import json

import httpx
import pytest

from core.ring.client import RingClient, RingError, RingMediaNotFound
from core.ring.live_view import look_at_door, minimal_whep_offer, wait_for_event

REQ_MS = 100_000  # FakeClock starts at 100.0 s


class FakeClock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s


def test_offer_is_valid_looking_sdp():
    sdp = minimal_whep_offer()
    assert sdp.startswith("v=0\r\n") and sdp.endswith("\r\n")
    assert "m=video" in sdp and "a=recvonly" in sdp
    assert sdp.count("a=ice-ufrag:") == 2
    assert minimal_whep_offer() != sdp  # fresh ICE/DTLS ids each time


def build(snapshot_codes=(303,), event_pages=()):
    calls = []
    snaps = iter(snapshot_codes)
    pages = iter(event_pages)

    def api(request):
        if request.url.path.endswith("/events"):
            calls.append(("events",))
            return httpx.Response(200, json={"data": next(pages)})
        if request.url.path.endswith("/whep/sessions"):
            calls.append(("whep_post",))
            return httpx.Response(201, text="v=0", headers={"Location": "/v1/devices/D/media/streaming/whep/sessions/S"})
        if request.method == "DELETE":
            calls.append(("whep_delete",))
            return httpx.Response(200)
        if request.url.path.endswith("/image/download"):
            calls.append(("snapshot", json.loads(request.content)["timestamp"]))
            code = next(snaps)
            if code == 416:
                return httpx.Response(416, json={"errors": [{"code": "MEDIA_NOT_FOUND"}]})
            return httpx.Response(303, headers={"Location": "https://signed.example.com/img"})
        return httpx.Response(404)

    async def no_sleep(s):
        pass

    ring = RingClient("tok", transport=httpx.MockTransport(api),
                      media_transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"\xff\xd8\xffJPEG")),
                      sleep=no_sleep)
    return ring, calls, FakeClock()


async def test_look_snapshots_inside_live_window_then_closes_session():
    ring, calls, clock = build()
    async with ring:
        look = await look_at_door(ring, "D", clock=clock, sleep=clock.sleep)
    assert look.snapshot.startswith(b"\xff\xd8\xff")
    assert look.requested_at == REQ_MS
    assert look.snapshot_ts == REQ_MS + 3000
    assert look.event is None
    assert calls == [("whep_post",), ("snapshot", REQ_MS + 3000), ("whep_delete",)]
    assert clock.t >= 100.0 + 3.0 + 1.5  # waited until the frame exists


async def test_look_retries_later_frame_on_416():
    ring, calls, clock = build(snapshot_codes=(416, 303))
    async with ring:
        look = await look_at_door(ring, "D", clock=clock, sleep=clock.sleep)
    assert look.snapshot_ts == REQ_MS + 4000
    assert calls[-1] == ("whep_delete",)


async def test_look_closes_session_even_when_no_media():
    ring, calls, clock = build(snapshot_codes=(416, 416, 416))
    async with ring:
        with pytest.raises(RingMediaNotFound):
            await look_at_door(ring, "D", clock=clock, sleep=clock.sleep)
    assert calls[-1] == ("whep_delete",)


def ev(id_, start):
    return {"type": "history-events", "id": id_, "attributes": {"event_type": "on_demand", "start": start, "end": start + 40000}}


async def test_wait_for_event_matches_by_start_time():
    old = ev("old", REQ_MS - 60_000)
    ours = ev("ours", REQ_MS + 2300)
    ring, _, clock = build(event_pages=[[old], [old], [ours, old]])
    async with ring:
        look = await look_at_door(ring, "D", clock=clock, sleep=clock.sleep)
        clock.t = 100.0  # make the window relative to the request
        e = await wait_for_event(ring, look, clock=clock, sleep=clock.sleep)
    assert e["id"] == "ours"
    assert look.event_id == "ours"


async def test_wait_for_event_times_out():
    ring, _, clock = build(event_pages=[[ev("old", 1)]] * 100)
    async with ring:
        look = await look_at_door(ring, "D", clock=clock, sleep=clock.sleep)
        with pytest.raises(RingError, match="no on_demand event"):
            await wait_for_event(ring, look, wait_s=10, poll_s=3, clock=clock, sleep=clock.sleep)
