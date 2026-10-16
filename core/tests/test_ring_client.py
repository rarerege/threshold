"""Offline tests for core.ring.client using httpx.MockTransport and recorded, redacted fixtures."""

import json
from pathlib import Path

import httpx
import pytest

from core.ring.client import RingAuthError, RingClient, RingError, RingMediaNotFound, redact

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://api.amazonvision.com"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_client(handler, media_handler=None, sleeps=None):
    async def fake_sleep(s):
        if sleeps is not None:
            sleeps.append(s)

    return RingClient(
        "test-token-abcdefghijkl",
        transport=httpx.MockTransport(handler),
        media_transport=httpx.MockTransport(media_handler or (lambda r: httpx.Response(500))),
        sleep=fake_sleep,
    )


def test_redact_truncates_to_8_chars():
    assert redact("abcdefghijklmnop") == "abcdefgh…"
    assert redact("") == "<none>"


def test_missing_token_is_a_clear_error():
    with pytest.raises(RingAuthError, match="not set"):
        RingClient("")


async def test_401_says_token_expired():
    async with make_client(lambda r: httpx.Response(401, json={"errors": []})) as ring:
        with pytest.raises(RingAuthError, match="generate a new one"):
            await ring.list_devices()


async def test_429_honours_retry_after_then_succeeds():
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"data": []})

    async with make_client(handler, sleeps=sleeps) as ring:
        assert await ring.list_devices() == []
    assert len(calls) == 3
    assert sleeps == [2.0, 2.0]  # Retry-After (2s) beats backoff 0.5s, 1.0s


async def test_429_without_retry_after_uses_exponential_backoff_and_gives_up():
    sleeps = []
    ring = make_client(lambda r: httpx.Response(429), sleeps=sleeps)
    ring._max_retries = 3
    async with ring:
        with pytest.raises(RingError) as ei:
            await ring.list_devices()
    assert ei.value.status == 429
    assert sleeps == [0.5, 1.0, 2.0]


async def test_get_retries_transient_5xx():
    codes = iter([503, 504, 200])
    sleeps = []

    def handler(request):
        code = next(codes)
        return httpx.Response(code, json={"data": []} if code == 200 else {"errors": []})

    async with make_client(handler, sleeps=sleeps) as ring:
        assert await ring.list_devices() == []
    assert sleeps == [0.5, 1.0]


async def test_post_does_not_retry_5xx():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    async with make_client(handler) as ring:
        with pytest.raises(RingError) as ei:
            await ring.start_live_view("D", "v=0")
    assert ei.value.status == 503
    assert len(calls) == 1


async def test_get_retries_transport_timeout():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("timed out")
        return httpx.Response(200, json={"data": []})

    async with make_client(handler) as ring:
        assert await ring.list_devices() == []
    assert len(calls) == 2


async def test_bearer_header_sent_to_api():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"data": []})

    async with make_client(handler) as ring:
        await ring.list_devices()
    assert seen["auth"] == "Bearer test-token-abcdefghijkl"


async def test_snapshot_follows_303_without_bearer_header():
    media_seen = {}

    def api(request):
        assert request.url.path == "/v1/devices/DEVICE_ID/media/image/download"
        assert json.loads(request.content) == {"type": "at_timestamp", "timestamp": 1234}
        return httpx.Response(303, headers={"Location": "https://media.example.com/signed?sig=x"})

    def media(request):
        media_seen["auth"] = request.headers.get("Authorization")
        media_seen["host"] = request.url.host
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"\xff\xd8\xffjpeg")

    async with make_client(api, media) as ring:
        jpg = await ring.get_snapshot("DEVICE_ID", 1234)
    assert jpg.startswith(b"\xff\xd8\xff")
    assert media_seen == {"auth": None, "host": "media.example.com"}


@pytest.mark.parametrize("where", ["api", "media"])
async def test_snapshot_416_raises_media_not_found(where):
    def api(request):
        if where == "api":
            return httpx.Response(416, json={"errors": [{"code": "MEDIA_NOT_FOUND"}]})
        return httpx.Response(303, headers={"Location": "https://media.example.com/x"})

    async with make_client(api, lambda r: httpx.Response(416, text="MEDIA_NOT_FOUND")) as ring:
        with pytest.raises(RingMediaNotFound):
            await ring.get_snapshot("DEVICE_ID", 1)


async def test_get_events_follows_links_next():
    pages = {
        None: {"data": [{"id": "e1", "attributes": {"event_type": "on_demand", "start": 1}}],
               "links": {"next": "/v1/history/devices/D/events?page[key]=2026-10-16T00:00:00Z"}},
        "2026-10-16T00:00:00Z": {"data": [{"id": "e2", "attributes": {"event_type": "on_demand", "start": 5}}],
                                 "links": {}},
    }

    def handler(request):
        return httpx.Response(200, json=pages[request.url.params.get("page[key]")])

    async with make_client(handler) as ring:
        events = await ring.get_events("D")
        latest = await ring.latest_on_demand_event("D")
    assert [e["id"] for e in events] == ["e1", "e2"]
    assert latest["id"] == "e2"


async def test_refuses_to_send_token_to_foreign_host():
    def handler(request):
        return httpx.Response(200, json={"data": [], "links": {"next": "https://evil.example.com/steal"}})

    async with make_client(handler) as ring:
        with pytest.raises(RingError, match="foreign host"):
            await ring.get_events("D")


async def test_live_view_start_and_stop():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.headers.get("content-type")))
        if request.method == "POST":
            return httpx.Response(201, text="v=0\r\nanswer", headers={"Location": "/v1/devices/D/media/streaming/whep/sessions/S1"})
        return httpx.Response(200)

    async with make_client(handler) as ring:
        s = await ring.start_live_view("D", "v=0\r\noffer")
        await ring.stop_live_view(s)
    assert s.session_url == f"{BASE}/v1/devices/D/media/streaming/whep/sessions/S1"
    assert s.sdp_answer.startswith("v=0")
    assert calls == [
        ("POST", "/v1/devices/D/media/streaming/whep/sessions", "application/sdp"),
        ("DELETE", "/v1/devices/D/media/streaming/whep/sessions/S1", None),
    ]


# --- recorded fixtures (from scripts/probe_live_view.py, ids redacted) ----------

def needs_fixture(name):
    return pytest.mark.skipif(not (FIXTURES / name).exists(), reason=f"{name} not recorded yet")


@needs_fixture("devices.json")
async def test_list_devices_from_recorded_fixture():
    async with make_client(lambda r: httpx.Response(200, json=load("devices.json"))) as ring:
        devices = await ring.list_devices()
    assert len(devices) == 1
    assert devices[0]["id"] == "DEVICE_ID"
    assert devices[0]["attributes"]["name"] == "Playground Device"


@needs_fixture("events_page.json")
async def test_latest_on_demand_from_recorded_fixture():
    async with make_client(lambda r: httpx.Response(200, json=load("events_page.json"))) as ring:
        ev = await ring.latest_on_demand_event("DEVICE_ID")
    assert ev is not None
    assert ev["type"] == "history-events"
    assert ev["attributes"]["event_type"] == "on_demand"
    assert ev["attributes"]["end"] >= ev["attributes"]["start"]
