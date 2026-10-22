# AWS Builder mini-challenge: how Threshold uses AWS

Threshold uses two AWS pieces:

1. **Kiro Crew** built a feature of the project: voice input and spoken replies in the web client. Per the challenge rules, building with Kiro Crew qualifies on its own.
2. **Amazon Bedrock (Converse API with tool use)** is integrated as the router that maps a caregiver's words to one MCP tool. It's implemented and tested offline. We could not run it live against Bedrock before the deadline because our AWS account's activation was blocked at the payment-verification step. We say so plainly instead of implying a live run.

## 1. Kiro Crew: voice input and spoken replies

**What we asked for.** On 22 Oct 2026, on a dedicated branch (`kiro/voice-input`), we gave Kiro Crew one bounded task. The task text was:

- Add a mic button and a "Speak replies" option to the web client (`web/index.html`, `web/app.js`) using the browser Web Speech API:
  - `SpeechRecognition` with `lang="en-IN"` and interim results;
  - the final transcript goes through the existing `ask()` path;
  - `speechSynthesis` reads replies aloud.
- Constraints:
  - no new dependencies and no build step;
  - don't touch `.env`, `core/`, `scripts/`, `web/mcp-client.js` or `web/router.js`;
  - run `node --check`, the router tests and the Python tests;
  - commit on the branch without pushing or merging.

**What Kiro Crew produced.** Commit `33ae80f` *"Web: voice input and spoken replies (built with Kiro Crew)"*:

- `web/index.html`: a mic button (hidden when speech recognition is unsupported), a listening style and a "Speak replies" checkbox.
- `web/app.js`: speech recognition wired into the existing form submit (interim text shown live, the final text submitted) and speech synthesis for replies.
- `README.md`: a short usage section.

It stayed inside the allowed files. Its only side effect was a local `.kiro/settings/` folder, which is now git-ignored.

**What review found and changed.** Commit `5adfcaf`:

| Finding | How we checked | Fix |
|---|---|---|
| Spoken replies read the placeholder (`"tools/call get_routine_status Working…"`), not the answer. The observer fired when a bubble was *added*, before the tool result filled it in. | In the browser, with `speechSynthesis.speak` patched to record its input | An explicit `speak()` call where the final answer is known. In Bedrock mode only the model's answer is spoken. Re-checked: it speaks *"No. Dad hasn't checked in today; the deadline was 10:30. …"* |
| A blocked microphone failed silently | Code review | The chat now says "Voice input stopped: microphone permission was denied…" |
| The README section had been inserted inside the Bedrock setup steps | Code review | Moved after them |

**Tests after review:** Python 75 passed; router tests 6/6; `node --check web/app.js` OK.

**Not verified:** actual speech recognition needs a real microphone and the user's permission prompt.

**Screenshots** (Kiro Crew session; add these as PNGs, since `*.jpg` is git-ignored):

- `docs/img/kiro-crew-task.png`: the task given to the agent
- `docs/img/kiro-crew-activity.png`: the agent's tool calls and results
- `docs/img/kiro-crew-summary.png`: the agent's final summary

## 2. Amazon Bedrock Converse: tool-use router

**Where:** `core/orchestrator/bedrock.py`, `core/orchestrator/http.py`, `scripts/bedrock_gate.py`, `scripts/eval_bedrock.py`.

**How it works:**

1. The page sends the caregiver's words to `POST /assistant`.
2. The server calls Bedrock Converse. The 7 MCP tools are offered as `toolSpec`s generated from MCP `tools/list`.
3. The server returns the tool the model chose. The **browser runs it as a real MCP `tools/call`**.
4. The browser posts the result back, and a second Converse turn writes a short answer grounded in it.

**Guardrails, in code rather than the prompt:**

- Only the MCP tools can be called.
- `confirm` is hidden from `pause_monitoring` and stripped from model input, so a pause needs a human click.
- Snapshots are never sent to the model.
- At most 2 tool calls per question.
- Any clock time in the answer must appear in the tool result, or the tool's own text is shown instead.

**Evidence:**

- **Field names** were taken from the installed botocore service model (`bedrock-runtime` 2023-09-30), not from memory.
- **15 offline tests** use botocore's Stubber. Every stubbed request is checked against the Converse input shape. We found that the Stubber skips request validation (`docs/friction-log.md`, FL-06), so the test fixture adds it.
- **The web page's Bedrock flow** was exercised end to end in a browser against scripted Converse responses. It showed:
  - the routing round trip;
  - the model's `confirm: true` being stripped, with monitoring left on;
  - an invented time ("06:45") replaced by the tool's text.
- **Fallback:** when Bedrock isn't configured, the page falls back to the keyword router and says why (19/25 on `web/eval_phrases.json`).

**Not done:** a live Converse call. `scripts/bedrock_gate.py` will run it as soon as AWS credentials work. It lists the Anthropic models and profiles the account can use, makes a two-turn tool-use round trip, and reports `stopReason`, latency and token usage.
