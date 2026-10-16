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

