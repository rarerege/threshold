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

Live Ring tests are skipped when `RING_ACCESS_TOKEN` isn't set.

## Run

*TODO (Phases 2–4):* Ring smoke test, MCP server, web client.

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
