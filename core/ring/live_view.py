"""Create an on_demand Ring event and pull a snapshot from inside it, without any WebRTC stack.

Verified on the Playground (docs/gates.md, 16 Oct 2026): WHEP signaling alone (a syntactically
valid SDP offer, no ICE/DTLS, no media) makes the device record an ~40 s on_demand event whose
start is ~2.3 s after the POST. DELETE-ing the session early does not shorten the event, and the
event only shows up in history after it ends, but its media can be downloaded while it runs.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core.ring.client import RingClient, RingError, RingMediaNotFound

SNAPSHOT_OFFSET_MS = 2000  # frame offset from an event's start when snapshotting an existing event
EVENT_MATCH_SLACK_MS = 2000  # clock skew allowance before the request time
EVENT_MATCH_WINDOW_MS = 15000  # observed start is ~2.3 s after the request


def minimal_whep_offer() -> str:
    """Offer mirroring the official sample (audio sendrecv, video recvonly), with random ICE/DTLS ids."""
    ufrag = secrets.token_hex(4)
    pwd = secrets.token_hex(12)
    fp = ":".join(f"{b:02X}" for b in secrets.token_bytes(32))
    common = [
        "c=IN IP4 0.0.0.0", "a=rtcp:9 IN IP4 0.0.0.0",
        f"a=ice-ufrag:{ufrag}", f"a=ice-pwd:{pwd}", "a=ice-options:trickle",
        f"a=fingerprint:sha-256 {fp}", "a=setup:actpass",
    ]
    lines = [
        "v=0", f"o=- {secrets.randbelow(10**18)} 2 IN IP4 127.0.0.1", "s=-", "t=0 0",
        "a=group:BUNDLE 0 1", "a=msid-semantic: WMS",
        "m=audio 9 UDP/TLS/RTP/SAVPF 111", *common, "a=mid:0", "a=sendrecv", "a=rtcp-mux",
        "a=rtpmap:111 opus/48000/2", "a=fmtp:111 minptime=10;useinbandfec=1",
        "m=video 9 UDP/TLS/RTP/SAVPF 96 102", *common, "a=mid:1", "a=recvonly", "a=rtcp-mux", "a=rtcp-rsize",
        "a=rtpmap:96 VP8/90000", "a=rtcp-fb:96 nack", "a=rtcp-fb:96 nack pli",
        "a=rtpmap:102 H264/90000", "a=rtcp-fb:102 nack pli",
        "a=fmtp:102 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
    ]
    return "\r\n".join(lines) + "\r\n"


@dataclass
class DoorLook:
    device_id: str
    snapshot: bytes
    snapshot_ts: int
    requested_at: int  # epoch ms when the live view was requested
    event: dict[str, Any] | None = None  # filled by wait_for_event(); listed only after the ~40 s window ends

    @property
    def event_id(self) -> str | None:
        return self.event["id"] if self.event else None


async def look_at_door(
    ring: RingClient,
    device_id: str,
    *,
    snapshot_delay_ms: int = 3000,
    settle_s: float = 1.5,
    attempts: int = 3,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> DoorLook:
    """Start a live view and take a snapshot from inside the on_demand event it creates.

    The event starts ~2.3 s after the request; its media is downloadable while the live view is
    still open (~8 s end to end), long before the event is listed in history (~43 s).
    """
    requested_at = int(clock() * 1000)
    session = await ring.start_live_view(device_id, minimal_whep_offer())
    try:
        for i in range(attempts):
            snapshot_ts = requested_at + snapshot_delay_ms + i * 1000
            await sleep(max(0.0, (snapshot_ts / 1000 + settle_s) - clock()))
            try:
                jpg = await ring.get_snapshot(device_id, snapshot_ts)
                break
            except RingMediaNotFound:
                if i == attempts - 1:
                    raise
    finally:
        await ring.stop_live_view(session)
    return DoorLook(device_id, jpg, snapshot_ts, requested_at)


async def wait_for_event(
    ring: RingClient,
    look: DoorLook,
    *,
    wait_s: float = 90.0,
    poll_s: float = 3.0,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Find the on_demand event our live view created (start within EVENT_MATCH_WINDOW_MS of the request)."""
    lo = look.requested_at - EVENT_MATCH_SLACK_MS
    hi = look.requested_at + EVENT_MATCH_WINDOW_MS
    deadline = clock() + wait_s
    while True:
        for e in await ring.get_events(look.device_id):
            a = e.get("attributes", {})
            if a.get("event_type") == "on_demand" and lo <= a.get("start", 0) <= hi:
                look.event = e
                return e
        if clock() >= deadline:
            raise RingError(f"no on_demand event for the live view appeared within {wait_s:.0f}s")
        await sleep(poll_s)
