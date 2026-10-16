Ring gate — 16 Oct 2026, \~10:30 IST — PASS

Playground token from India: works (30-min lifetime)

GET /v1/devices: 1 synthetic "Playground Device" (Doorbell Pro image)

Event history: empty until a live view; live view creates an on\_demand event (\~40 s)

Live video: works via ring-api-helloworld (looping simulator scene, watermarked)

Snapshot: POST /media/image/download {"type":"at\_timestamp","timestamp":<ms>}

&#x20; → 303 → signed URL → 200 image/jpeg; outside an event window → 416 MEDIA\_NOT\_FOUND

&#x20; type also accepts latest\_in\_range

Not available: motion/button\_press events, webhooks (need registered app + US staging user)




Python live-view gate — 16 Oct 2026, \~14:30 IST — PASS (no aiortc / no WebRTC stack)

WHEP POST /v1/devices/{id}/media/streaming/whep/sessions with a hand-built SDP offer (random ICE/DTLS ids, no candidates, no media) → SDP answer + Location (session URL incl. `?location=` routing param); DELETE on that URL → OK

That signaling alone creates an on\_demand event: start ≈ request + 2.3 s, duration ≈ 40 s regardless of DELETE after 5 s or 15 s

The event is listed in history only after it ends (≈ 43–45 s after the request), but at\_timestamp snapshots inside it work while the live view is open → snapshot in ≈ 8.6–9.9 s end to end

Snapshots of older on\_demand events still work (tested: 4.2 h old → 200 image/jpeg)

Gateway flakiness: transient 503/504/500 (empty body) on GETs for \~1 min, then recovered → client retries idempotent calls
