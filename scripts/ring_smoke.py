r"""Ring smoke test: device count, newest events, and a snapshot saved to data\snapshot.jpg.

By default snapshots the newest existing on_demand event; starts a live view only if that fails
or with --look.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.ring.client import RingClient, RingError, RingMediaNotFound  # noqa: E402
from core.ring.live_view import SNAPSHOT_OFFSET_MS, look_at_door, wait_for_event  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "snapshot.jpg"


def fmt(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S") if ms else "-"


async def main(force_look: bool, wait_event: bool) -> int:
    async with RingClient.from_env() as ring:
        devices = await ring.list_devices()
        print(f"devices: {len(devices)}")
        if not devices:
            return 1
        dev = next((d for d in devices if d["attributes"].get("name") == "Playground Device"), devices[0])
        device_id = dev["id"]
        print(f"device: {dev['attributes'].get('name')!r}")

        events = sorted(await ring.get_events(device_id), key=lambda e: e["attributes"].get("start", 0), reverse=True)
        print(f"events: {len(events)}")
        for e in events[:3]:
            a = e["attributes"]
            print(f"  {a.get('event_type'):10} start={fmt(a.get('start'))} end={fmt(a.get('end'))}")

        jpg = None
        latest = next((e for e in events if e["attributes"].get("event_type") == "on_demand"), None)
        if latest and not force_look:
            ts = latest["attributes"]["start"] + SNAPSHOT_OFFSET_MS
            age_min = (time.time() * 1000 - ts) / 60000
            try:
                jpg = await ring.get_snapshot(device_id, ts)
                print(f"snapshot from newest event ({age_min:.0f} min old): {len(jpg)} bytes")
            except RingMediaNotFound:
                print(f"newest event ({age_min:.0f} min old) has no media (416); starting a live view")

        if jpg is None:
            t0 = time.time()
            look = await look_at_door(ring, device_id)
            jpg = look.snapshot
            print(f"live view -> snapshot {len(jpg)} bytes in {time.time() - t0:.1f}s "
                  f"(frame at {fmt(look.snapshot_ts)})")
            if wait_event:
                e = await wait_for_event(ring, look)
                print(f"on_demand event listed after {time.time() - t0:.1f}s: "
                      f"start={fmt(e['attributes']['start'])} end={fmt(e['attributes'].get('end'))}")

        OUT.parent.mkdir(exist_ok=True)
        OUT.write_bytes(jpg)
        print(f"saved {OUT} (jpeg={jpg[:3] == b'\xff\xd8\xff'})")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--look", action="store_true", help="always start a fresh live view")
    p.add_argument("--wait-event", action="store_true", help="after a live view, wait (~45 s) for its event to be listed")
    a = p.parse_args()
    try:
        sys.exit(asyncio.run(main(a.look, a.wait_event)))
    except RingError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
