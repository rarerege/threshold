r"""Probe: does WHEP signaling alone (hand-built SDP offer, no ICE/media) create an on_demand event?

Also records redacted API responses into core/tests/fixtures/ for offline unit tests.
Usage: .\.venv\Scripts\python.exe scripts\probe_live_view.py [--hold 15] [--wait 60]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.ring.client import RingClient, RingError, redact  # noqa: E402
from core.ring.live_view import minimal_whep_offer  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "core" / "tests" / "fixtures"


class Redactor:
    """Replace ids consistently so fixtures keep their structure but leak nothing."""

    def __init__(self, device_id: str):
        self.map = {device_id: "DEVICE_ID"}

    def _sub(self, s: str) -> str:
        for real, fake in self.map.items():
            s = s.replace(real, fake)
        return s

    def __call__(self, obj):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k == "id" and isinstance(v, str) and v not in self.map:
                    self.map[v] = f"ID_{len(self.map)}"
                out[k] = self(v)
            return out
        if isinstance(obj, list):
            return [self(v) for v in obj]
        if isinstance(obj, str):
            return self._sub(obj)
        return obj


def save_fixture(name: str, data) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / name).write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  fixture -> {(FIXTURES / name).relative_to(ROOT)}")


async def main(hold: int, wait: int) -> int:
    ring = RingClient.from_env()
    print(f"token={redact(os.environ.get('RING_ACCESS_TOKEN'))}")
    async with ring:
        raw_devices = (await ring._request("GET", "/v1/devices")).json()
        devices = raw_devices.get("data", [])
        dev = next((d for d in devices if d.get("attributes", {}).get("name") == "Playground Device"), devices[0])
        device_id = dev["id"]
        red = Redactor(device_id)
        print(f"devices={len(devices)} using name={dev['attributes'].get('name')!r} id={redact(device_id)}")
        save_fixture("devices.json", red(raw_devices))

        raw_before = (await ring._request("GET", f"/v1/history/devices/{device_id}/events")).json()
        before = {e["id"] for e in raw_before.get("data", [])}
        print(f"events before: {len(before)} (links={list((raw_before.get('links') or {}).keys())})")

        offer = minimal_whep_offer()
        t0 = int(time.time() * 1000)
        try:
            session = await ring.start_live_view(device_id, offer)
        except RingError as e:
            print(f"WHEP POST failed: status={e.status} body={e.body[:500]}")
            return 2
        print(f"WHEP POST ok: session={red._sub(session.session_url)}")
        print("answer (first 8 lines):\n  " + "\n  ".join(session.sdp_answer.splitlines()[:8]))
        print(f"holding {hold}s ...")
        await asyncio.sleep(hold)
        try:
            await ring.stop_live_view(session)
            print("WHEP DELETE ok")
        except RingError as e:
            print(f"WHEP DELETE failed: status={e.status} body={e.body[:300]}")

        deadline = time.time() + wait
        new = []
        while time.time() < deadline:
            raw_after = (await ring._request("GET", f"/v1/history/devices/{device_id}/events")).json()
            new = [e for e in raw_after.get("data", []) if e["id"] not in before]
            if new:
                break
            await asyncio.sleep(5)
        if not new:
            print(f"NO new event within {wait}s after DELETE (signaling-only does not create an on_demand event)")
            save_fixture("events_page.json", red(raw_after))
            return 1

        ev = new[0]
        a = ev["attributes"]
        print(f"NEW event: type={a.get('event_type')} start={a.get('start')} end={a.get('end')} "
              f"(start - POST time = {a.get('start', 0) - t0} ms)")
        save_fixture("events_page.json", red(raw_after))

        ts = a["start"] + 2000
        try:
            jpg = await ring.get_snapshot(device_id, ts)
            out = ROOT / "data" / "probe_snapshot.jpg"
            out.parent.mkdir(exist_ok=True)
            out.write_bytes(jpg)
            print(f"snapshot @start+2s: {len(jpg)} bytes, magic={jpg[:3].hex()} -> {out.relative_to(ROOT)}")
        except RingError as e:
            print(f"snapshot failed: status={e.status} body={e.body[:300]}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--hold", type=int, default=15)
    p.add_argument("--wait", type=int, default=60)
    args = p.parse_args()
    sys.exit(asyncio.run(main(args.hold, args.wait)))
