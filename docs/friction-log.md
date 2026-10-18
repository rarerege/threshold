\# Friction Log — Threshold (Amazon Developer Hackathon 2026)



\## FL-01 — Snapshot request body is undocumented in the Playground

\- \*\*Date:\*\* 16 Oct 2026, \~10:00 IST

\- \*\*Tool:\*\* Ring Partner API — POST /v1/devices/{id}/media/image/download (Playground token)

\- \*\*Task attempted:\*\* Download a still image from the Playground Device.

\- \*\*Steps taken:\*\*

&#x20; 1. Sent an empty body `{}` → 403 REQUEST\_FORBIDDEN "missing required timestamp fields in request body".

&#x20; 2. Added `timestamp` (epoch ms) → 400 INVALID\_PAYLOAD "type is required".

&#x20; 3. Added `"type":"image"` → 400 "type not supported, valid values: at\_timestamp, latest\_in\_range".

&#x20; 4. Used `"type":"at\_timestamp"` → 303 redirect → 416 MEDIA\_NOT\_FOUND (no footage at that time).

&#x20; 5. Started a live view first, then used a timestamp inside the resulting on\_demand event → 200 image/jpeg.

\- \*\*Expected:\*\* The Playground (or the API reference) shows the request body schema and a working example.

\- \*\*Actual:\*\* The schema could only be discovered by trial and error across 4 failed requests. A missing-field error returned 403, not 400, which suggested a permission problem.

\- \*\*Severity:\*\* Major (≈30 min lost).

\- \*\*Workaround:\*\* Read each error message and add fields one at a time.

\- \*\*Suggestion:\*\* Add an "Image Snapshot" tab to the Playground with a sample body. Document the `type` values (`at\_timestamp`, `latest\_in\_range`) and their fields. Return 400 rather than 403 for missing body fields.



\## FL-02 — Playground Device has no events, so motion and doorbell flows can't be tested

\- \*\*Date:\*\* 16 Oct 2026, \~09:45 IST

\- \*\*Tool:\*\* Ring Developer Playground — Event History tab

\- \*\*Task attempted:\*\* Read event history to build a routine baseline from motion and doorbell events.

\- \*\*Steps taken:\*\* Generated a token → Event History → Invoke → `{"data": \[]}`. No tab or control exists to simulate a motion or button-press event.

\- \*\*Expected:\*\* The synthetic device ships with sample events (motion, button\_press) or a "simulate event" control, so event-driven apps can be tested without US hardware.

\- \*\*Actual:\*\* History is empty. The only way to create an event is to start a live view, which produces an `on\_demand` event. Motion and button\_press can't be tested; webhooks need a registered app plus a US-located staging device.

\- \*\*Severity:\*\* Major for event-driven and caretaking use cases; we had to redesign around on-demand checks.

\- \*\*Workaround:\*\* Trigger live views to create on\_demand events; use voice check-ins as the routine signal.

\- \*\*Suggestion:\*\* Seed the Playground Device with a rolling set of sample events, and add "simulate motion / doorbell press" buttons that also fire signed test webhooks.


\## FL-03 — MCP Python SDK 2.x renamed FastMCP to MCPServer

\- \*\*Date:\*\* 16 Oct 2026, \~14:20 IST

\- \*\*Tool:\*\* MCP Python SDK (`mcp` 2.3.0 from PyPI)

\- \*\*Task attempted:\*\* Run a minimal FastMCP server over Streamable HTTP for the Phase 0 MCP gate.

\- \*\*Steps taken:\*\*

&#x20; 1. `pip install "mcp\[cli]"` installed 2.3.0.

&#x20; 2. `from mcp.server.fastmcp import FastMCP` → ModuleNotFoundError, pointing to the v2 migration guide: FastMCP was renamed to `MCPServer` (`from mcp.server.mcpserver import MCPServer`).

&#x20; 3. Switched to `MCPServer` and ran `run(transport="streamable-http", streamable\_http\_path="/mcp")`. initialize with `2025-11-25` negotiated `2025-11-25`. The SDK's LATEST\_PROTOCOL\_VERSION is `2026-07-28`.

\- \*\*Expected:\*\* `FastMCP` (as in most tutorials and hackathon guidance) imports from the latest SDK.

\- \*\*Actual:\*\* A breaking rename in 2.x. The error message was clear and linked the migration guide.

\- \*\*Severity:\*\* Minor (≈5 min).

\- \*\*Workaround:\*\* Use `MCPServer` from `mcp.server.mcpserver` (or pin `mcp<2`).

\- \*\*Suggestion:\*\* Hackathon docs should name the SDK major version and note that the 2026-07-28 spec changes the handshake (`initialized` notification and `ping` removed), which matters for hand-written MCP clients.


\## FL-04 — Transient 5xx from the API gateway right after a live view

\- \*\*Date:\*\* 16 Oct 2026, \~14:30 IST

\- \*\*Tool:\*\* Ring Partner API (Playground token) — GET /v1/history/devices/{id}/events, GET /v1/devices

\- \*\*Task attempted:\*\* Poll event history after closing a WHEP session, to find the new on\_demand event.

\- \*\*Steps taken:\*\*

&#x20; 1. GET events → 503 "upstream connect error or disconnect/reset before headers. reset reason: connection timeout".

&#x20; 2. GET /v1/devices → 504 `{"errors":\[{"status":"504","title":"Gateway Timeout"}]}`.

&#x20; 3. Retried with backoff → 500 with an empty body, then 200 about 10 s later; stable afterwards.

\- \*\*Expected:\*\* 2xx, or a 503 with Retry-After if the backend is busy.

\- \*\*Actual:\*\* About a minute of mixed 500/503/504 with no Retry-After. The 500 had no error body, so it couldn't be told apart from a real server error.

\- \*\*Severity:\*\* Minor (≈5 min; the first probe run crashed).

\- \*\*Workaround:\*\* The client retries 500/502/503/504 and transport timeouts with exponential backoff, for idempotent methods only (GET/DELETE). POSTs are never retried automatically.

\- \*\*Suggestion:\*\* Return a JSON:API error body and a Retry-After on every 5xx, and document which 5xx responses are safe to retry.



\## FL-05 — Live view → event behaviour is undocumented (and the event is listed late)

\- \*\*Date:\*\* 16 Oct 2026, \~14:35 IST

\- \*\*Tool:\*\* Ring Partner API — WHEP sessions + Event History + image/download

\- \*\*Task attempted:\*\* From Python, create an on\_demand event and snapshot the door, quickly enough for a voice answer.

\- \*\*Steps taken:\*\*

&#x20; 1. Sent a WHEP POST with a hand-built SDP offer (no WebRTC stack, no ICE) → success (non-error status; exact code not logged) with an SDP answer and a Location session URL; DELETE OK.

&#x20; 2. Polled history: the new on\_demand event appeared only ≈ 43–45 s after the request, about when its ≈ 40 s window ended. Our first "wait for event, then snapshot" flow took 50 s.

&#x20; 3. Requested an at\_timestamp snapshot at request + 3 s while the session was still open → 200 image/jpeg; ready in ≈ 9 s.

\- \*\*Expected:\*\* Docs say what creates an on\_demand event (media flowing, or signaling alone), how long it lasts, and when it becomes visible in history.

\- \*\*Actual:\*\* All three had to be found by experiment. Signaling alone is enough; the length is fixed at ≈ 40 s even after an early DELETE; listing lags until the event ends.

\- \*\*Severity:\*\* Major for latency-sensitive use (≈45 min spent finding a fast path).

\- \*\*Workaround:\*\* Snapshot inside the live window right away; match the event afterwards by start time (request − 2 s … request + 15 s) and attach it as evidence.

\- \*\*Suggestion:\*\* Return the on\_demand event id in the WHEP response (header or body), list in-progress events with `end: null`, and document event timing and retention.


\## FL-06 — botocore Stubber skips request parameter validation

\- \*\*Date:\*\* 18 Oct 2026, \~22:30 IST

\- \*\*Tool:\*\* botocore 1.43.114 `botocore.stub.Stubber` with the bedrock-runtime client (Converse)

\- \*\*Task attempted:\*\* Unit-test the Bedrock router offline and rely on botocore to reject any Converse field the real API doesn't define.

\- \*\*Steps taken:\*\*

&#x20; 1. Wrote a test calling `client.converse(modelId=..., messages=[], toolChoices={...})` (misspelt field) under an active Stubber, expecting `ParamValidationError`.

&#x20; 2. Got `UnStubbedResponseError` instead: the Stubber intercepts the call before parameter validation runs, so the bogus field was accepted.

&#x20; 3. Checked the other direction: `add_response` *does* validate stubbed responses against the output shape (a missing `usage`/`metrics` raised `ParamValidationError`).

\- \*\*Expected:\*\* Stubbed calls validate request parameters the same way real calls do.

\- \*\*Actual:\*\* Responses are validated; requests are not (unless `expected_params` is given, which only compares values).

\- \*\*Severity:\*\* Minor (≈10 min), but it would have silently hidden invented request fields.

\- \*\*Workaround:\*\* The test fixture registers a `provide-client-params.bedrock-runtime.Converse` hook that calls `botocore.validate.validate_parameters(params, operation_model.input_shape)`; a test asserts the misspelt field now fails.

\- \*\*Suggestion:\*\* Validate request params in Stubber by default (or offer `Stubber(client, validate_params=True)`), and say so in the Stubber docs.

