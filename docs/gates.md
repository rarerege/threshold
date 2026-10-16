Ring gate — 16 Oct 2026, \~10:30 IST — PASS

Playground token from India: works (30-min lifetime)

GET /v1/devices: 1 synthetic "Playground Device" (Doorbell Pro image)

Event history: empty until a live view; live view creates an on\_demand event (\~40 s)

Live video: works via ring-api-helloworld (looping simulator scene, watermarked)

Snapshot: POST /media/image/download {"type":"at\_timestamp","timestamp":<ms>}

&#x20; → 303 → signed URL → 200 image/jpeg; outside an event window → 416 MEDIA\_NOT\_FOUND

&#x20; type also accepts latest\_in\_range

Not available: motion/button\_press events, webhooks (need registered app + US staging user)

