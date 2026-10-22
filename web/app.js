import { McpClient } from "./mcp-client.js";
import { route } from "./router.js";

const $ = (s) => document.querySelector(s);
const MCP_URL = new URL("mcp", location.href).href;
const POLL_MS = 20000;

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) n.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined) n.append(c);
  return n;
}

// --- JSON-RPC debug drawer ------------------------------------------------------

let rpcCount = 0;
function redactImages(value) {
  return JSON.parse(JSON.stringify(value, (k, v) =>
    k === "data" && typeof v === "string" && v.length > 200 ? `<${v.length} base64 chars>` : v));
}
function onTraffic(e) {
  rpcCount++;
  $("#rpc-count").textContent = rpcCount;
  const reply = e.replies[0];
  const isErr = e.status >= 400 || reply?.error || reply?.result?.isError;
  const label = e.request.method + (e.request.params?.name ? ` ${e.request.params.name}` : "");
  const item = el("details", { class: "rpc", "data-quiet": e.quiet ? "1" : "" },
    el("summary", {},
      el("span", {}, e.request.id ? `#${e.request.id}` : "notify"),
      el("strong", {}, label),
      el("span", { class: isErr ? "s bad" : "s" }, `HTTP ${e.status}${reply?.result?.isError ? " · isError" : ""}`),
      el("span", { class: "muted" }, `${e.ms} ms`)),
    el("pre", {}, "→ " + JSON.stringify(e.request, null, 2)),
    el("pre", {}, e.replies.length ? "← " + JSON.stringify(redactImages(e.replies.length === 1 ? e.replies[0] : e.replies), null, 2)
                                   : "← (no body)"));
  if (e.quiet && !$("#show-polling").checked) item.hidden = true;
  $("#traffic").prepend(item);
  $("#session-info").textContent = e.sessionId ? `Mcp-Session-Id ${e.sessionId.slice(0, 8)}…` : "";
}
function logAssistant(request, status, body, ms) {
  rpcCount++;
  $("#rpc-count").textContent = rpcCount;
  const bad = status >= 400;
  const what = body?.type === "tool_call" ? `→ ${body.name}` : body?.type || "";
  $("#traffic").prepend(el("details", { class: "rpc" },
    el("summary", {}, el("span", {}, "Bedrock"), el("strong", {}, `POST /assistant ${what}`),
      el("span", { class: bad ? "s bad" : "s" }, `HTTP ${status}`),
      el("span", { class: "muted" }, `${ms} ms${body?.latency_ms ? ` (model ${body.latency_ms} ms)` : ""}`)),
    el("pre", {}, "→ " + JSON.stringify(request, null, 2)),
    el("pre", {}, "← " + JSON.stringify(body, null, 2))));
}
$("#drawer-toggle").addEventListener("click", () => {
  const open = $("#drawer").classList.toggle("open");
  $("#drawer-toggle").setAttribute("aria-expanded", String(open));
});
$("#show-polling").addEventListener("change", (ev) => {
  for (const d of document.querySelectorAll('details.rpc[data-quiet="1"]')) d.hidden = !ev.target.checked;
});

// --- chat -------------------------------------------------------------------------

const log = $("#log");
function say(cls, ...children) {
  const m = el("div", { class: `msg ${cls}` }, ...children);
  log.append(m);
  m.scrollIntoView({ block: "end", behavior: "smooth" });
  return m;
}

function ladder(alert) {
  return el("ul", { class: "ladder" }, alert.history.map((s) =>
    el("li", { class: s.to_state === "resolved" ? "resolved" : "" },
      el("span", { class: "t" }, s.time_local), `${s.to_state.replaceAll("_", " ")} — ${s.note}`)));
}

function renderResult(tool, args, result) {
  const sc = result.structuredContent || {};
  const text = result.content?.find((c) => c.type === "text")?.text || "(no text)";
  const parts = [el("span", { class: "tool" }, `tools/call ${tool}`), el("div", {}, text)];

  for (const img of result.content?.filter((c) => c.type === "image") || []) {
    parts.push(el("img", { src: `data:${img.mimeType};base64,${img.data}`, alt: `Door snapshot at ${sc.snapshot_time_local || ""}` }));
    parts.push(el("div", { class: "caption" },
      `${sc.device_name || "Ring"} · frame ${new Date(sc.snapshot_ts).toLocaleTimeString()} · Ring event ${sc.ring_event_id || "pending (listed ~45 s after the live view)"} · `,
      el("a", { href: sc.snapshot_url, target: "_blank", rel: "noopener" }, "open image")));
  }
  if (tool === "get_routine_status" || tool === "record_checkin") {
    for (const a of sc.open_alerts || []) parts.push(el("div", { class: "caption" }, `Open alert (${a.kind.replaceAll("_", " ")}):`), ladder(a));
  }
  if (tool === "acknowledge_alert" && sc.history) parts.push(ladder(sc));
  if (tool === "get_day_summary") {
    const items = [
      ...sc.checkins.map((c) => [c.time_local, `check-in: ${c.status.replaceAll("_", " ")}${c.note ? ` (“${c.note}”)` : ""}`]),
      ...sc.alerts.map((a) => [a.opened_at_local, `alert (${a.kind.replaceAll("_", " ")}): ${a.history.map((h) => h.to_state.replaceAll("_", " ")).join(" → ")}`]),
      ...sc.door_looks.map((d) => [d.time_local, el("span", {}, "door snapshot · ", el("a", { href: d.snapshot_url, target: "_blank", rel: "noopener" }, "view"))]),
    ].sort((x, y) => x[0].localeCompare(y[0]));
    parts.splice(1, 1, el("div", {}, `${sc.date}:`));
    parts.push(items.length ? el("ul", {}, items.map(([t, what]) => el("li", {}, el("span", { class: "muted" }, t + " "), what)))
                            : el("div", { class: "muted" }, "Nothing recorded yet."));
    if (sc.expected_visits.length) parts.push(el("div", { class: "caption" },
      "Expected today: " + sc.expected_visits.map((v) => `${v.label} ${v.start}–${v.end}`).join(", ")));
  }
  if (tool === "pause_monitoring" && sc.status === "needs_confirmation") {
    const actions = el("div", { class: "actions" });
    const done = (msg) => { actions.replaceChildren(el("span", { class: "muted" }, msg)); };
    actions.append(
      el("button", { class: "btn primary", onclick: () => { done("Confirmed."); runTool("pause_monitoring", { ...args, confirm: true }); } },
         args.hours === 0 ? "Yes, resume" : `Yes, pause ${args.hours} h`),
      el("button", { class: "btn", onclick: () => done("Cancelled. Nothing changed.") }, "Cancel"));
    parts.push(actions);
  }
  return parts;
}

let client;
async function runTool(tool, args, { speakResult = true } = {}) {
  const waiting = say("bot", el("span", { class: "tool" }, `tools/call ${tool}`),
    el("div", { class: "pending" }, tool === "check_the_door" ? "Starting a live view and taking a snapshot (about 10 s)…" : "Working…"));
  let result;
  try {
    result = await client.callTool(tool, args);
    waiting.replaceChildren(...renderResult(tool, args, result));
    if (result.isError) waiting.classList.add("err");
  } catch (e) {
    result = { isError: true, content: [{ type: "text", text: `Error: ${e.message}` }] };
    waiting.replaceChildren(el("span", { class: "tool" }, `tools/call ${tool}`), el("div", {}, `Error: ${e.message}`));
    waiting.classList.add("err");
  }
  waiting.scrollIntoView({ block: "end", behavior: "smooth" });
  refreshStatus();
  if (speakResult) speak(result.content?.find((c) => c.type === "text")?.text);
  return result;
}

// --- routing: Bedrock Converse (server-side) with the keyword router as fallback ---------

const routerState = { mode: "keywords", bedrock: null, conversationId: null };

async function postAssistant(body) {
  const started = performance.now();
  const res = await fetch("assistant", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({ type: "error", reason: `HTTP ${res.status}` }));
  logAssistant(body, res.status, data, Math.round(performance.now() - started));
  if (res.status === 503 || data.type === "unavailable") throw Object.assign(new Error(data.reason || "Bedrock unavailable"), { fallback: true });
  if (!res.ok) throw new Error(data.reason || `HTTP ${res.status}`);
  return data;
}

async function askBedrock(text) {
  let r = await postAssistant({ conversation_id: routerState.conversationId, text });
  routerState.conversationId = r.conversation_id;
  while (r.type === "tool_call") {
    // The page itself runs tools/call over MCP; only Bedrock's final answer is spoken.
    const result = await runTool(r.name, r.input, { speakResult: false });
    const textOut = result.content?.find((c) => c.type === "text")?.text || "";
    r = await postAssistant({ conversation_id: r.conversation_id, tool_result: {
      tool_use_id: r.tool_use_id, text: textOut, structured: result.structuredContent ?? null, is_error: !!result.isError } });
  }
  const label = `Bedrock · ${r.model_id}${r.source === "tool_text" ? ` · used tool text (${r.note})` : ""}`;
  say("bot", el("span", { class: "tool" }, label), el("div", {}, r.text));
  speak(r.text);
}

function askKeywords(text, note) {
  const r = route(text);
  if (note) say("bot", el("div", { class: "caption" }, note));
  if (r.kind === "tool") return runTool(r.tool, r.args);
  if (r.kind === "help") {
    say("bot", el("div", {}, r.text));
    speak(r.text);
  }
}

async function ask(text) {
  say("user", text);
  if (routerState.mode !== "bedrock") return askKeywords(text);
  try {
    await askBedrock(text);
  } catch (e) {
    if (!e.fallback) return say("bot err", el("div", {}, `Error: ${e.message}`));
    setRouter("keywords", `Bedrock unavailable: ${e.message}`);
    await askKeywords(text, `Bedrock unavailable (${e.message}); used the keyword router.`);
  }
}

function setRouter(mode, reason = "") {
  routerState.mode = mode;
  $("#router").value = mode;
  $("#router-note").textContent = mode === "bedrock" ? `${routerState.bedrock.model_id} · ${routerState.bedrock.region}` : reason;
}

async function initRouter() {
  try {
    routerState.bedrock = await (await fetch("assistant/status")).json();
  } catch {
    routerState.bedrock = { available: false, reason: "status endpoint unreachable" };
  }
  $("#router").querySelector('option[value="bedrock"]').disabled = !routerState.bedrock.available;
  if (routerState.bedrock.available) setRouter("bedrock");
  else setRouter("keywords", `Bedrock off: ${routerState.bedrock.reason}`);
}
$("#router").addEventListener("change", (ev) => setRouter(ev.target.value, "keyword router selected"));

$("#ask").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  $("#q").value = "";
  ask(q);
});
$("#suggest").addEventListener("click", (ev) => { if (ev.target.matches("button")) ask(ev.target.textContent); });
for (const b of document.querySelectorAll("[data-checkin]")) {
  b.addEventListener("click", () => {
    say("user", `(Dad) ${b.textContent}`);
    runTool("record_checkin", { status: b.dataset.checkin, note: b.dataset.checkin === "needs_help" ? "said they need help" : "" });
  });
}

// --- status panel -------------------------------------------------------------------

let statusBusy = false;
async function refreshStatus() {
  if (!client?.sessionId || statusBusy) return;
  statusBusy = true;
  try {
    const r = await client.callTool("get_routine_status", {}, { quiet: true });
    const s = r.structuredContent;
    const box = $("#status");
    box.replaceChildren(
      el("span", { class: `state ${s.routine_state}` }, s.routine_state.replaceAll("_", " ")),
      el("dl", { class: "kv" },
        el("dt", {}, "Check in by"), el("dd", {}, `${s.checkin_by} (alert after ${s.deadline_local})`),
        el("dt", {}, "Last check-in"), el("dd", {}, s.last_checkin ? `${s.last_checkin.time_local} · ${s.last_checkin.status.replaceAll("_", " ")}` : "none today"),
        el("dt", {}, "Monitoring"), el("dd", {}, s.monitoring_paused ? `paused (${s.pause_reason || "no reason"})` : "on")),
      ...s.open_alerts.map((a) => el("div", {}, el("div", { class: "caption" }, `Open alert · ${a.kind.replaceAll("_", " ")}`), ladder(a))));
    $("#status-updated").textContent = `Updated ${s.now_local} (parent's time) · refreshes every ${POLL_MS / 1000} s`;
  } catch (e) {
    $("#status-updated").textContent = `Status unavailable: ${e.message}`;
  } finally {
    statusBusy = false;
  }
}

// --- connection ---------------------------------------------------------------------

async function connect() {
  $("#reconnect").hidden = true;
  $("#conn .dot").className = "dot";
  $("#conn-text").textContent = "Connecting…";
  client = new McpClient(MCP_URL, onTraffic);
  try {
    const init = await client.connect();
    $("#conn .dot").className = "dot on";
    $("#conn-text").textContent = `${init.serverInfo.name} · MCP ${client.protocolVersion} · ${client.tools.length} tools`;
    if (!log.childElementCount) {
      say("bot", el("div", {}, `Connected to ${init.serverInfo.title || init.serverInfo.name}. Tools: `,
        el("code", {}, client.tools.map((t) => t.name).join(", ")), ". Ask me about Dad."));
    }
    refreshStatus();
  } catch (e) {
    $("#conn .dot").className = "dot off";
    $("#conn-text").textContent = `Not connected: ${e.message}`;
    $("#reconnect").hidden = false;
  }
}
$("#reconnect").addEventListener("click", connect);
setInterval(refreshStatus, POLL_MS);
connect();
initRouter();

// --- voice input + spoken replies ------------------------------------------------

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const micBtn = $("#mic");
const qInput = $("#q");
const speakReplies = $("#speak-replies");

// Show the mic button only if speech recognition is supported
if (SR) micBtn.hidden = false;

let recog = null;
let listening = false;
let finalText = "";

function setListening(on) {
  listening = on;
  micBtn.classList.toggle("listening", on);
  micBtn.setAttribute("aria-label", on ? "Stop listening" : "Voice input");
}

if (SR) {
  micBtn.addEventListener("click", () => {
    if (listening) { recog.stop(); return; }
    finalText = "";
    recog = new SR();
    recog.lang = "en-IN";
    recog.interimResults = true;
    recog.onstart = () => setListening(true);
    recog.onend = () => {
      setListening(false);
      // Submit the final transcript through the existing ask() path
      if (finalText.trim()) {
        qInput.value = finalText.trim();
        $("#ask").requestSubmit();
      }
    };
    recog.onerror = (ev) => {
      setListening(false);
      if (ev.error === "no-speech" || ev.error === "aborted") return;  // user was silent or stopped
      const why = ev.error === "not-allowed" || ev.error === "service-not-allowed"
        ? "microphone permission was denied" : ev.error;
      say("bot err", el("div", {}, `Voice input stopped: ${why}. You can still type your question.`));
    };
    recog.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i];
        if (t.isFinal) finalText += t[0].transcript;
        else interim += t[0].transcript;
      }
      // Show interim text in the input box as the user speaks
      qInput.value = finalText + interim;
    };
    recog.start();
  });
}

// Speak a reply aloud when "Speak replies" is on. Called with the final answer text only:
// a bubble is added as a "Working…" placeholder and filled in later, so watching the log
// for new bubbles would read the placeholder (and the tools/call label) instead of the answer.
function speak(text) {
  if (!speakReplies?.checked || !window.speechSynthesis || !text) return;
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "en-IN";
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(u);
}
