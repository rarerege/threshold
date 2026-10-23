# Devpost submission: Threshold

Copy-paste source for every field. Deadline: **Fri 23 Oct 2026, 12:00 PT (Sat 24 Oct 00:30 IST)**.

## Checklist

| # | Item | Required? | Status | Who |
|---|---|---|---|---|
| 1 | Text description | yes | drafted below (§1) | paste |
| 2 | GitHub repo with source, assets, run instructions and a license, *or* private and shared | yes | ready locally; **not pushed** (§2) | you create the repo, I push |
| 3 | Demo video under 3 min, public on YouTube/Vimeo, in English | yes | script below (§3) | you record |
| 4 | Product feedback on every tool/API/SDK, including how AWS was used | yes | drafted (§4) | paste |
| 5 | Tracks and mini challenges, plus their extra fields | yes | drafted (§5) | paste |
| 6 | What was built during the window | yes, if prior work | drafted (§6) | confirm window start |
| 7 | Feature requests | optional | drafted (§7) | paste |
| 8 | Friction log (up to a 10% bonus) | optional | 8 entries (§8, `docs/friction-log.md`) | paste |

---

## 1. Text description

> For Devpost's **project story** field, paste `docs/devpost-story.md`. It uses only basic Markdown (headings, bold, lists, links, inline code), which Devpost renders reliably. The summary below is for shorter description fields.

**Threshold: a caretaking agent for adult children whose older parent lives alone.**

Every morning, Dad hears "Good morning, how are you today?" and answers "I'm OK" or "I need help". If he doesn't answer by the time his family set (e.g. 10:00 plus a 30-minute grace period), Threshold runs a fixed, audited escalation ladder:

**ok → checking in** (ask Dad again) **→ caregiver alerted → secondary contact alerted → resolved**

Every step is timestamped and can be acknowledged. A "need help" answer goes straight to the caregiver.

The caregiver just asks in plain language:
- *"Did Dad check in today?"* gives the state, from the database.
- *"Can I see Dad's door?"* starts a **Ring** live view, which creates an `on_demand` event. Threshold pulls a snapshot from inside that event and shows it within about 10 seconds.
- *"What happened today?"*, *"I'm on it"*, *"Carer visits Tue and Fri 11 to 12"* and *"Pause for the weekend"* (which needs confirmation) work the same way.

**What it deliberately doesn't do:** no face recognition, no identity claims ("I can't tell who anyone is, only show the picture"), and no automatic emergency calls. Alerts are decided by **deterministic rules**, never by the LLM. The model only routes speech to a tool and phrases a short answer grounded in the tool's result.

**How it works:**
- **MCP server:** spec **2025-11-25**, **Streamable HTTP** at `/mcp`, built with the official MCP Python SDK. Seven tools (`record_checkin`, `get_routine_status`, `get_day_summary`, `add_expected_visit`, `acknowledge_alert`, `pause_monitoring`, `check_the_door`), each with an input schema, an output schema, structured results and annotations. `pause_monitoring` changes nothing until it receives `confirm=true`.
- **Alexa+ simulator:** our own web page is a real MCP client written against the wire protocol (`initialize` → `notifications/initialized` → `tools/list` → `tools/call`, SSE parsing, session headers). It has a live JSON-RPC debug drawer, voice input and spoken replies.
- **Ring:**
  - an async client for `api.amazonvision.com`;
  - live view via WHEP signaling only: we found that a hand-built SDP offer creates the `on_demand` event without any WebRTC stack;
  - an `at_timestamp` snapshot inside the live window;
  - the event id linked as evidence once Ring lists it (about 45 s later);
  - retries and backoff on 429 and transient 5xx responses.
- **Engine:** check-in window rule → decision → escalation ladder, with SQLite audit rows. Steps are stamped with the time they were *due*, so a late or restarted evaluator produces the same audit trail. 75 Python tests run on a fake clock.
- **AWS:**
  - **Kiro Crew** built the voice feature;
  - an **Amazon Bedrock Converse** tool-use router (guardrails in code; keyword fallback) is implemented and tested offline. Live use was blocked by AWS account activation; see §4.

---

## 2. Repository

- **Contents:** all source code (`core/`, `web/`, `scripts/`), recorded and redacted test fixtures, docs, run steps (README → "Demo: clean run"), and an **MIT LICENSE**.
- **Never committed:** `.env`, tokens, snapshots and the database are git-ignored, and each commit was scanned for tokens and ids.
- **Choose one:**
  - **Public plus open-source license.** Our MIT license already covers this. Simplest, and it also suits the Open Source mini challenge.
  - **Private, shared** with `testing@devpost.com` and the Amazon team: knmeiss, giolaq, anishamalde, mosesroth, emersonsklar. Add them at submission time; invitations expire after 7 days.
- **Where each track's technology is called in code:**
  - **Alexa+:** the MCP server is in `core/mcp_server/server.py` (`MCPServer`, `/mcp` Streamable HTTP) and `core/mcp_server/__main__.py`. The simulation source is in `web/` (`mcp-client.js`, `app.js`).
  - **Ring:** `core/ring/client.py` and `core/ring/live_view.py` (devices, events, WHEP live view, snapshot download). They're used by the `check_the_door` tool.

---

## 3. Demo video (under 3:00, best material first, no music)

**Setup before recording:**
1. Main demo, port 8000, fresh `data\demo.db`, Dad due in 60 min: `.\scripts\demo_main.ps1`
2. Ladder demo, port 8001, fresh `data\ladder.db`, steps a minute apart: run `.\scripts\demo_ladder.ps1` about 3 minutes before recording that segment.
3. Paste a **fresh Ring token** into `.env` right before recording (it lasts 30 min).
4. Open both pages in Chrome.

| Time | Show | Say (roughly) |
|---|---|---|
| 0:00–0:15 | Title, then the page | "My dad lives alone. Threshold checks in with him every morning, and lets me look in, without cameras on him and without guesswork." |
| 0:15–0:50 | Type or say **"Can I see Dad's door?"** → the snapshot appears. Open the JSON-RPC drawer: `tools/call check_the_door`. | "That's a real Ring live view on the Ring Developer Playground. The live view creates an `on_demand` event, and Threshold pulls a frame from inside it. It doesn't say who anyone is; it only shows the picture." |
| 0:50–1:20 | **"Did Dad check in today?"** → *not yet*. Click **I'm OK** in Dad's panel, then ask again → *yes*. | "Dad answers his morning check-in by voice. The answer comes from the database, not from the AI's memory." |
| 1:20–1:55 | Switch to the :8001 tab. The Today panel shows checking in → caregiver alerted → secondary alerted with times. Type **"I'm on it"** → resolved. | "If he doesn't answer, a fixed ladder runs: ask him again, alert me, then a second contact. Rules decide alerts, not the AI. Every step is logged." |
| 1:55–2:15 | Mic button: say "What happened today?" with **Speak replies** on. | "Voice in, voice out, using the browser's speech API." |
| 2:15–2:40 | Drawer: `initialize` with `protocolVersion 2025-11-25`, `tools/list` with 7 tools. Briefly show `core/mcp_server/server.py`. | "Under the hood it's an MCP server on the 2025-11-25 spec over Streamable HTTP. This page is a real MCP client." |
| 2:40–2:55 | `docs/aws-builder.md` | "The voice feature was built with Kiro Crew. A Bedrock Converse router is ready behind a keyword fallback." |

Upload as **public** (or unlisted, if the rules allow) on YouTube, in English. Don't add music. The Ring watermark comes from the Playground's own simulator feed.

---

## 4. Product feedback (every tool, API and SDK)

**MCP Python SDK (`mcp` 2.3.0) and the MCP spec 2025-11-25**
- *Used for:* the Threshold MCP server: 7 tools, output schemas, tool annotations and Streamable HTTP.
- *Worked well:* declaring each tool's output schema as `Annotated[CallToolResult, Model]` gave us validated structured output plus our own text and image blocks. DNS-rebinding protection is on by default for localhost. Sessions and SSE worked first time with raw `curl`.
- *Needs work:* the 2.x rename from `FastMCP` to `MCPServer` breaks most tutorials (FL-03). The error message, which links the migration guide, was excellent. The 2026-07-28 revision drops `notifications/initialized` and `ping`, which matters for hand-written clients, and it's easy to miss.
- *Onboarding:* about 30 minutes to a working gate, including the rename.
- *Again?* Yes.

**Ring Partner API and Developer Playground**
- *Used for:* devices, event history, WHEP live view, and the snapshot download that answers "Can I see the door?".
- *Worked well:* the Playground token is instant (30-minute lifetime), and there's a synthetic device. The official Next.js sample made WHEP easy to understand. Signed media URLs are simple.
- *Needs work:*
  - The snapshot request body is undocumented, and a missing field returns 403 rather than 400 (FL-01).
  - The Playground device has no motion or doorbell events and no way to simulate them (FL-02).
  - Live view → event timing is undocumented, and events are listed only after their ~40 s window ends (FL-05).
  - Transient 5xx responses come without `Retry-After` (FL-04).
- *Onboarding:* fast for reads; slow for media (about 1.5 h of trial and error, all logged).
- *Again?* Yes, especially with event simulation and webhooks in the Playground.

**Kiro Crew (AWS)**
- *Used for:* building the voice input and spoken-reply feature as a delegated, reviewed agent task on its own branch.
- *Worked well:*
  - the Windows gateway setup, once signed in;
  - the agent stayed inside the file boundaries, ran the exact checks we asked for, and wrote a clear summary;
  - it flagged correctly that the microphone needs a real browser.
- *Needs work:* the checks passed, but the spoken-reply behaviour was wrong. It read the "Working…" placeholder, which only a browser check catches (FL-08). The installation docs we read listed macOS and Linux builds, while the download page also offered Windows.
- *Onboarding:* about 20 minutes: download, run the sign-in command it showed, then the dashboard.
- *Again?* Yes, for bounded tasks on a review branch.

**Amazon Bedrock (Converse API, tool use) and boto3/botocore**
- *Used for:* the router that maps a caregiver's words to one MCP tool and phrases a grounded answer. Code: `core/orchestrator/bedrock.py`.
- *Worked well:* the botocore service model is a precise, offline source of truth for Converse shapes (`toolSpec`, `toolUse`, `toolResult`, `stopReason`). The Stubber validates responses.
- *Needs work:*
  - Account activation stalled at payment verification, so we never got a live call (FL-07).
  - It isn't clear whether free-plan accounts can use Bedrock or Anthropic models.
  - The Stubber silently skips *request* validation (FL-06).
- *Onboarding:* blocked at the account step.
- *Again?* Yes. The integration is ready, and the gate script will confirm it once credentials work.

**AWS services used, and how:**
- **Kiro Crew** built a shipped feature; see `docs/aws-builder.md` (commits `33ae80f`, `5adfcaf`; screenshots in `docs/img/`).
- **Amazon Bedrock Converse** is integrated as the tool-use router: implemented, offline-tested and exercised in the browser with scripted responses. Not called live (account activation).

**Also used:** httpx (async Ring client), Pydantic, SQLite, uvicorn and pytest. All smooth. tzdata was needed on Windows for `zoneinfo`. The browser Web Speech API handles voice.

---

## 5. Tracks and mini challenges

- **Primary track: Alexa+.** Our own MCP server (2025-11-25, Streamable HTTP), shown working through our own web MCP client, the simulated Alexa+ experience. The source is in `web/`.
- **Second track: Ring.** Real Ring API calls in code, demonstrated on the Ring Developer Playground simulator.
- **Mini challenge: AWS Builder.** Built with Kiro Crew, which qualifies on its own, plus a documented Bedrock Converse integration. Documentation: `docs/aws-builder.md`.
- **Mini challenge: Open Source.** Only if we finish it. It needs a *new, additional* project or a contribution to a public repo. Candidate: a botocore fork and PR adding opt-in request validation to the Stubber (FL-06). Fields to fill in: contribution URL ___, repo URL ___, GitHub username ___, description: *"Adds opt-in request-parameter validation to botocore's Stubber, so stubbed tests catch fields the real API doesn't define. Found while testing a Bedrock Converse integration, where a misspelt field passed silently."*

---

## 6. Built during the submission window

Threshold was built from scratch between **16 and 23 Oct 2026**. The first commit, 16 Oct, was the scaffold; the history has 20+ small commits. Nothing in the repo predates the hackathon.

Used but not included: Ring's official `ring-api-helloworld` sample app, which we ran only to confirm that live view works on the Playground.

*(Confirm the hackathon window started on or before 16 Oct; otherwise adjust this.)*

---

## 7. Feature requests

*(Basic Markdown only, no tables, so it renders on Devpost.)*

1. **Ring Playground: seed sample motion and doorbell events, and add "simulate event" buttons that fire signed test webhooks.** Urgency: **Critical**. Event-driven and caretaking apps can't be tested without US hardware; we had to redesign around on-demand checks (FL-02).
2. **Ring: return the `on_demand` event id in the WHEP response, and list in-progress events with `end: null`.** Urgency: **Important**. Apps could attach evidence right away instead of polling for about 45 s (FL-05).
3. **Ring: document the snapshot request body and `type` values; return 400 rather than 403 for missing fields.** Urgency: **Important**. Saves about 30 minutes of trial and error per developer (FL-01).
4. **AWS: hackathon sandbox accounts or credits with Bedrock enabled, and clear free-plan service limits.** Urgency: **Important**. Payment verification blocked our live Bedrock use (FL-07).
5. **Kiro Crew: optional browser-based verification for web tasks.** Urgency: **Nice-to-have**. Unit checks passed on a UI bug (FL-08).
6. **botocore Stubber: validate request parameters by default.** Urgency: **Nice-to-have**. Stubbed tests silently accept invented fields (FL-06).

---

## 8. Friction log

There are 8 entries, each with the date, tool, task, steps, expected vs actual, severity, workaround and suggestion. Full log: https://github.com/rarerege/threshold/blob/main/docs/friction-log.md

- **FL-01, Ring snapshot API (Major):** the request body is undocumented, and a missing field returns 403 rather than 400.
- **FL-02, Ring Playground (Major):** there are no motion or doorbell events, and no way to simulate them.
- **FL-03, MCP Python SDK (Minor):** 2.x renamed `FastMCP` to `MCPServer`.
- **FL-04, Ring API gateway (Minor):** transient 500/503/504 errors without `Retry-After`.
- **FL-05, Ring live view (Major):** event timing is undocumented, and events are listed about 45 s late.
- **FL-06, botocore Stubber (Minor):** stubbed calls skip request validation.
- **FL-07, AWS account (Critical for Bedrock):** activation blocked live Bedrock use.
- **FL-08, Kiro Crew (Minor):** the agent's checks passed on a UI behaviour bug.

If Devpost asks for each entry separately, copy the fields for each one from the full log.
