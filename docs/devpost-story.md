## Inspiration

Millions of adult children have a parent who lives alone, often in another city. The daily worry is simple: *is Dad okay this morning?* Existing answers are either intrusive (cameras pointed at people, constant location tracking) or unreliable (an AI that "thinks" something is wrong).

We wanted something calmer:
- a daily check-in the parent actually answers;
- a predictable escalation when they don't;
- a way to take a quick look at the front door without watching anyone.

## What it does

**Threshold** is a caretaking agent with two sides.

**For the parent:** every morning, a voice check-in asks "Good morning, how are you today?". They answer *I'm OK* or *I need help*.

**For the caregiver:** they ask in plain language:
- *"Did Dad check in today?"* returns the real state from the database.
- *"Can I see Dad's door?"* starts a **Ring** live view, which creates an `on_demand` event. Threshold pulls a snapshot from inside that event and shows it within about 10 seconds.
- *"What happened today?"*, *"I'm on it"*, *"Carer visits Tue and Fri 11 to 12"* and *"Pause for the weekend"* work the same way.

**If the parent doesn't answer** by the time the family set (plus a grace period), a fixed escalation ladder runs:

**ok → checking in** (ask the parent again) **→ caregiver alerted → secondary contact alerted → resolved**

Every step is timestamped, stored and can be acknowledged. "I need help" goes straight to the caregiver.

**What it deliberately doesn't do:** no face recognition, no identity claims ("I can't tell who anyone is, only show the picture"), and no automatic emergency calls. **Alerts are decided by deterministic rules, never by the AI.**

## How we built it

- **MCP server (Alexa+ track):** built with the official MCP Python SDK on spec **2025-11-25** over **Streamable HTTP** (`/mcp`).
  - Seven tools: `record_checkin`, `get_routine_status`, `get_day_summary`, `add_expected_visit`, `acknowledge_alert`, `check_the_door` and `pause_monitoring`.
  - Each has JSON Schema inputs, an output schema, structured results and tool annotations.
  - `pause_monitoring` changes nothing until it receives `confirm=true`.
- **Alexa+ simulator:** our own web page is a real MCP client written against the wire protocol (`initialize` → `notifications/initialized` → `tools/list` → `tools/call`, with SSE parsing and session headers). It includes:
  - a live JSON-RPC debug drawer;
  - voice input and spoken replies (Web Speech API);
  - a panel that simulates the parent's morning check-in.
- **Ring (Ring track):** an async client for the Ring API:
  - devices, event history with pagination, WHEP live view and snapshot download;
  - 429/5xx retries with backoff;
  - a clear message when the token expires.
- **Engine:** a check-in window rule produces a decision, which drives the escalation ladder, with an SQLite audit row for every step.
  - Each step is stamped with the time it was *due*, so a late or restarted evaluator writes the identical audit trail.
  - It's covered by fake-clock tests.
  - With check-in time \\(t_c\\), grace \\(g\\), re-ask window \\(r\\) and secondary delay \\(s\\), all set by the family (defaults \\(g = 30\\), \\(r = 10\\), \\(s = 20\\) minutes):

$$t_{\text{checking in}} = t_c + g \\quad t_{\text{caregiver}} = t_{\text{checking in}} + r \\quad t_{\text{secondary}} = t_{\text{caregiver}} + s$$
- **AWS:**
  - **Kiro Crew** built the voice feature as a delegated agent task on a review branch.
  - An **Amazon Bedrock Converse** tool-use router is implemented with guardrails written in code:
    - a tool allowlist;
    - the model can never confirm a pause;
    - no images are sent to the model;
    - a grounding check on clock times.
  - The router falls back to a keyword router when Bedrock isn't available.
- **Tests:** 75 Python tests plus JS router tests. Ring fixtures are recorded from the real API with ids redacted.

Stack: Python 3.12, the MCP Python SDK, httpx, Pydantic, SQLite, uvicorn and pytest; vanilla JS for the web client.

## Challenges we ran into

- **Making Ring "look at the door" fast.** The live view → event behaviour wasn't documented. We discovered that WHEP signaling alone, with a hand-built SDP offer and **no WebRTC stack**, creates the `on_demand` event. That event is only *listed* about 45 seconds later, after its ~40 s window ends, but its frames can be downloaded while it's still running. That took a door check from about 50 s to about 10 s.
- **The snapshot API** needed trial and error: the request body is undocumented, and a missing field returned 403 rather than 400.
- **The Ring Playground has no motion or doorbell events**, so we redesigned around on-demand checks and voice check-ins.
- **The MCP SDK 2.x** renamed `FastMCP` to `MCPServer`, which breaks most tutorials.
- **AWS account activation** stalled at payment verification, so we couldn't call Bedrock live before the deadline. We built and tested the integration offline against botocore's service model instead, and found that botocore's Stubber silently skips request validation.
- **Reviewing an AI agent's work:** Kiro Crew's own checks passed, but in the browser the spoken replies read the "Working…" placeholder. We caught it in review and fixed it.

All of these are written up in our friction log, which has 8 entries.

## Accomplishments that we're proud of

- A real, end-to-end vertical slice. A typed question becomes an MCP `tools/call`, which runs a real Ring Playground live view, and a real snapshot appears in the page.
- **Safety by design:** rules decide, the AI only routes and phrases, a human confirms the risky action, and there are no identity claims.
- An audit trail that is identical however late the evaluator runs.
- Every API field we used was checked against the real API or the official service model, not assumed.

## What we learned

- Simple, deterministic rules plus a clear audit trail earn more trust in caregiving than clever AI judgements.
- MCP's Streamable HTTP is pleasant to implement against directly. Writing our own client made the protocol concrete.
- Measure before optimising: discovering when Ring events become visible was the key to the 10-second door check.
- Delegating to an agent works best on a review branch with explicit checks, and a human still has to look at the behaviour.

## What's next for Threshold

- A live Bedrock Converse router as soon as our AWS account is active. The gate script is ready.
- Ring motion and doorbell events through webhooks, to log expected visits automatically.
- Real notifications (SMS or push) for the caregiver and secondary contact.
- A real Alexa+ integration when the gated tools open up.
- Multi-household support and caregiver handover notes.

## Links

- Code: https://github.com/rarerege/threshold
- AWS Builder write-up: https://github.com/rarerege/threshold/blob/main/docs/aws-builder.md
- Friction log: https://github.com/rarerege/threshold/blob/main/docs/friction-log.md
