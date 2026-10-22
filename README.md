# Threshold

A caretaking agent for adult children whose older parent lives alone.
Built for the Amazon Developer Hackathon 2026 (Alexa+ track, Ring track).

- **Morning voice check-in** for the parent. If there's no reply within a grace period, a deterministic escalation ladder runs:
  `ok → checking_in → caregiver_alerted → secondary_alerted → resolved`. Every step is audited and can be acknowledged.
- **Natural-language questions** from the caregiver ("Did Dad check in today?", "Can I see Dad's door?"), served as MCP tools.
- **Ring as an on-demand "look at the door"**: Threshold starts a live view, which creates an `on_demand` event, then pulls a snapshot from inside that event.
- No face recognition, no identity claims, no automatic emergency calls. Alerts come from rules; the LLM only routes speech to tools and writes grounded summaries.

> Status: work in progress. Sections marked *TODO* are filled in as each phase lands.

## Architecture

```
web/  (MCP client, Alexa+ simulator) ──Streamable HTTP──▶ core/mcp_server  (MCP spec 2025-11-25)
                                                          │
                       core/engine  (rules + escalation) ◀┤
                       core/store   (Pydantic + SQLite)  ◀┤
                       core/ring    (Ring API client)    ◀┘──▶ https://api.amazonvision.com
```

| Path | Purpose |
|---|---|
| `core/store/` | Shared contracts (`models.py`) and SQLite persistence (`db.py`) |
| `core/ring/` | Async Ring API client: devices, events, live view, snapshots |
| `core/engine/` | Check-in window rule and escalation ladder state machine |
| `core/mcp_server/` | MCP server (official Python SDK, `MCPServer`) |
| `core/orchestrator/` | Speech/text → tool routing (keyword router now, Bedrock Converse later) |
| `web/` | Browser MCP client with a raw JSON-RPC debug drawer |
| `docs/` | `gates.md` (verified capability gates) and `friction-log.md` |
| `scripts/` | Smoke tests and the Phase 0 MCP gate server |

## Requirements

- Windows 10/11 with PowerShell (other OSes work with the equivalent commands)
- Python 3.11+ (developed on 3.12)
- A Ring Developer Playground token for the Ring features

## Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Then edit `.env`: in Ring Developer Playground choose **Generate token** and paste the result as `RING_ACCESS_TOKEN`. Tokens expire after 30 minutes. `.env` is git-ignored; never commit it.

## Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Live Ring tests are skipped when `RING_ACCESS_TOKEN` isn't set. `-m "not live"` skips them explicitly; `-m live` runs only them. Unit tests use recorded, id-redacted fixtures in `core/tests/fixtures/`, which you can re-record with `scripts\probe_live_view.py`.

## Run

### Ring smoke test (Phase 2)

Needs a fresh token in `.env`.

```powershell
.\scripts\ring_smoke.ps1             # device count, newest events, snapshot of newest event -> data\snapshot.jpg
.\scripts\ring_smoke.ps1 -Look       # start a live view first (~10 s), snapshot from inside the new on_demand event
.\scripts\ring_smoke.ps1 -Look -WaitEvent   # ...and wait (~45 s) until that event is listed in history
```

How "look at the door" works without a WebRTC stack: Threshold sends a WHEP offer, which on its own makes the device record an `on_demand` event. It downloads an `at_timestamp` snapshot from inside that event while the session is still open, then closes the session. The event only appears in history after its ~40 s window ends, so its id is matched afterwards by start time. See `docs/gates.md` and FL-05.

### MCP server (Phase 3)

```powershell
.\.venv\Scripts\python.exe -m core.mcp_server          # http://127.0.0.1:8000/mcp (Streamable HTTP)
.\scripts\verify_mcp.ps1                               # raw JSON-RPC: initialize -> tools/list -> every tool
.\scripts\verify_mcp.ps1 -SkipDoor                     # same, without calling Ring
```

State lives in `data\threshold.db` (SQLite, git-ignored). Door snapshots are saved in `data\snapshots\` and served at `/snapshots/<id>.jpg`. The routine (parent name, time zone, check-in time, grace and escalation timings) comes from the `THRESHOLD_*` variables in `.env` the first time the household is created. Delete the DB to apply new values.

| Tool | Kind | What it does |
|---|---|---|
| `get_routine_status` | read | "Did Dad check in today?": today's state, deadline, open alerts with full history |
| `get_day_summary` | read | Check-ins, alerts, door snapshots and expected visits for a day |
| `record_checkin` | write | Parent's answer: `ok` / `needs_help` / `no_answer` |
| `add_expected_visit` | write | Recurring expected visit (label, HH:MM–HH:MM, weekdays) |
| `acknowledge_alert` | write | Caregiver acknowledges an alert, which stops escalation |
| `pause_monitoring` | write, **needs confirmation** | No change unless `confirm=true`; `hours=0` resumes |
| `check_the_door` | Ring | Live view → `on_demand` event → snapshot (inline image + URL), ~10 s |

How the escalation works. The rules are deterministic, in `core/engine/`, and covered by tests on a fake clock:

- No `ok`/`needs_help` check-in by `checkin_by + grace` creates a `missed_checkin` decision, and its alert enters **checking_in** (the parent is asked again).
- After `reprompt_min` it moves to **caregiver_alerted**; after `secondary_after_min` without an acknowledgement, to **secondary_alerted**.
- The parent answering `ok`, or the caregiver acknowledging, moves it to **resolved**.
- `needs_help` opens a `help_requested` alert directly at **caregiver_alerted**.
- Every step is a row in `alert_transitions`, stamped with the time it was *due*, so a late or restarted evaluator writes the same audit trail.
- A 30 s background timer applies due steps, and every tool call does too.

#### MCP Inspector

1. Start the server as above.
2. Run `npx @modelcontextprotocol/inspector` and open the URL it prints.
3. Set **Transport Type** to *Streamable HTTP* and **URL** to `http://127.0.0.1:8000/mcp`, then click **Connect**.
4. Open **Tools** → **List Tools**, pick a tool, fill in its arguments and click **Run Tool**.

The result pane shows the text block, the structured content (validated against the tool's output schema) and, for `check_the_door`, the image.

### Web MCP client / Alexa+ simulator (Phase 4)

The server also serves the web client on the same origin, so no CORS is needed: open **http://127.0.0.1:8000/**.

- The page is a real MCP client written against the wire protocol (`web/mcp-client.js`). It sends `initialize` (2025-11-25), then `notifications/initialized` and `tools/list`, then a `tools/call` for each question. It handles both JSON and SSE responses and echoes `Mcp-Session-Id` and `MCP-Protocol-Version` on every request.
- Typed questions go through a keyword router (`web/router.js`) that picks one tool. Bedrock Converse replaces it in the next phase.
- The **JSON-RPC** drawer at the bottom shows every raw request and response. Base64 image data is shortened there only.
- The right-hand panel simulates Dad's morning check-in (`record_checkin`) and shows today's status, polled every 20 s.
- `pause_monitoring` always asks for confirmation in the page before it calls the tool again with `confirm=true`.

Router tests (Node 18+; no packages needed):

```powershell
node --test web/router.test.mjs
```

### Bedrock Converse router (AWS Builder)

Bedrock replaces the keyword router as the page's default when it's configured. The page stays the MCP client:

1. The page sends the caregiver's words to `POST /assistant` on our server, which holds the AWS credentials.
2. The server calls **Bedrock Converse**, offering the 7 MCP tools as `toolSpec`s taken from `tools/list`, and returns the tool the model chose.
3. The page runs that tool itself via MCP `tools/call`, then posts the result back.
4. A second Converse turn writes a one- or two-sentence answer from that result.

Guardrails live in code (`core/orchestrator/bedrock.py`):

- Only the MCP tools can be called.
- `confirm` is hidden from `pause_monitoring` and stripped from model input, so only the Confirm button in the page can pause monitoring.
- Snapshots are never sent to the model.
- At most 2 tool calls per question.
- Any clock time in the model's answer must appear in the tool result; otherwise the tool's own text is shown instead.

If Bedrock isn't configured or errors, the page falls back to the keyword router and says so.

### Voice input and spoken replies (Phase 5)

The web client supports voice input and text-to-speech using the browser's Web Speech API (no dependencies).

- **Mic button** next to the Ask button: uses `window.SpeechRecognition` (or `webkitSpeechRecognition`) with `lang: "en-IN"` and `interimResults: true`. Interim text appears in the input box as you speak; the final transcript is submitted through the existing `ask()` function. Click again to stop. The button is hidden when speech recognition is unavailable (no errors shown).
- **Speak replies** checkbox (off by default): when on, the text of each new bot reply is read aloud with `window.speechSynthesis`. The JSON-RPC debug drawer is never spoken.

Both features need a real browser with microphone access. Voice input requires a user gesture and may prompt for microphone permission.

Setup:

1. Add AWS keys (an IAM user with `bedrock:InvokeModel` and `bedrock:List*` is enough) and `AWS_REGION` to `.env`.
2. Run the gate. It lists the Anthropic models and inference profiles you can use, makes a Converse tool-use round trip, and prints the `BEDROCK_MODEL_ID` line to add to `.env`:
   ```powershell
   .\.venv\Scripts\python.exe scripts\bedrock_gate.py
   ```
3. Compare routing accuracy on `web/eval_phrases.json`:
   ```powershell
   node scripts/eval_keywords.mjs                       # keyword router: 19/25
   .\.venv\Scripts\python.exe scripts\eval_bedrock.py   # Bedrock router
   ```

The server re-reads `.env` until Bedrock is available, so no restart is needed after adding keys. Offline tests (`core/tests/test_bedrock.py`) use botocore's Stubber, and every request is checked against the Converse input shape (see FL-06).

## Demo: clean run

```powershell
Remove-Item data\threshold.db -ErrorAction SilentlyContinue   # optional: start from an empty state
.\.venv\Scripts\python.exe -m core.mcp_server
```

1. Generate a fresh token in the Ring Developer Playground and paste it into `.env`. The server re-reads `.env`, so it doesn't need restarting.
2. Open http://127.0.0.1:8000/. The header should read `threshold · MCP 2025-11-25 · 7 tools`.
3. Ask **"Can I see Dad's door?"**. This calls `tools/call check_the_door`, which runs a Ring live view and shows a real snapshot from the Playground device in about 10–15 s.
4. Ask **"Did Dad check in today?"**. This calls `get_routine_status`, which answers from SQLite. Click **I'm OK** in Dad's panel and ask again to see it change.
5. Optionally, ask "What happened today?". About 45 s after a door check, its Ring `on_demand` event id is linked as evidence.

To see the escalation ladder live, run with a compressed schedule on a fresh DB:

```powershell
$env:THRESHOLD_DB='data/ladder.db'; $env:THRESHOLD_CHECKIN_BY=(Get-Date).AddMinutes(2).ToString('HH:mm')
$env:THRESHOLD_GRACE_MIN='0'; $env:THRESHOLD_REPROMPT_MIN='1'; $env:THRESHOLD_SECONDARY_AFTER_MIN='1'
.\.venv\Scripts\python.exe -m core.mcp_server
```

The Today panel then steps through checking in → caregiver alerted → secondary alerted, one minute apart. "I'm on it" acknowledges it.

### MCP gate (Phase 0)

```powershell
.\.venv\Scripts\python.exe scripts\gate_server.py
```

This serves a single `ping` tool at `http://127.0.0.1:8000/mcp`.

## Notes

- The MCP Python SDK 2.x renamed `FastMCP` to `MCPServer` (`from mcp.server.mcpserver import MCPServer`). See `docs/friction-log.md` FL-03.
- Ring API calls are server-side only; the browser can't call `api.amazonvision.com` directly because of CORS.

## License

MIT. See [LICENSE](LICENSE).
