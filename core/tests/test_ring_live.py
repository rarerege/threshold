"""Optional live test against the Ring Developer Playground. Skipped without a real RING_ACCESS_TOKEN.

Run only these: pytest -m live      Exclude them: pytest -m "not live"
"""

import os

import pytest
from dotenv import load_dotenv

from core.ring.client import RingClient
from core.ring.live_view import look_at_door

load_dotenv()
TOKEN = os.environ.get("RING_ACCESS_TOKEN", "")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not TOKEN or TOKEN.startswith("paste-"), reason="RING_ACCESS_TOKEN not set"),
]


async def test_live_look_at_door_returns_jpeg():
    async with RingClient.from_env() as ring:
        devices = await ring.list_devices()
        assert devices, "Playground should expose one device"
        look = await look_at_door(ring, devices[0]["id"])
    assert look.snapshot[:3] == b"\xff\xd8\xff"
    assert len(look.snapshot) > 10_000
