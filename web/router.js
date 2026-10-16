// Keyword router: maps a caregiver's typed (later: spoken) request to one MCP tool call.
// Deliberately simple and predictable; Bedrock Converse replaces it in the next phase.

const DAY_WORDS = {
  mon: /\bmon(day)?s?\b/, tue: /\btue(s|sday)?s?\b/, wed: /\bwed(nesday)?s?\b/, thu: /\bthu(rs|rsday)?s?\b/,
  fri: /\bfri(day)?s?\b/, sat: /\bsat(urday)?s?\b/, sun: /\bsun(day)?s?\b/,
};
const ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

function parseTime(h, m, ampm) {
  let hh = Number(h);
  if (ampm === "pm" && hh < 12) hh += 12;
  if (ampm === "am" && hh === 12) hh = 0;
  return `${String(hh).padStart(2, "0")}:${m || "00"}`;
}

export function parseVisit(text) {
  const t = text.toLowerCase();
  let days = ALL_DAYS.filter((d) => DAY_WORDS[d].test(t));
  if (/\b(every ?day|daily)\b/.test(t)) days = [...ALL_DAYS];
  if (/\bweekdays\b/.test(t)) days = ["mon", "tue", "wed", "thu", "fri"];
  if (/\bweekends?\b/.test(t)) days = ["sat", "sun"];
  const times = [...t.matchAll(/\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b/g)].filter((m) => Number(m[1]) <= 23);
  if (times.length < 2 || !days.length) return null;
  const [a, b] = times;
  const endAmPm = b[3] || null;
  const start = parseTime(a[1], a[2], a[3] || endAmPm);
  const end = parseTime(b[1], b[2], endAmPm);
  const labelMatch = text.match(/\b(?:the|a|an|our|his|her)?\s*([A-Za-z][\w-]*)\s+(?:visits|comes|will come|is coming|every|on)\b/i);
  const stop = new Set(["add", "expect", "expected", "visit", "a", "the", "and", "he", "she", "someone"]);
  let label = labelMatch && !stop.has(labelMatch[1].toLowerCase()) ? labelMatch[1] : "Visit";
  label = label.charAt(0).toUpperCase() + label.slice(1);
  return { label, start, end, days };
}

export function route(input) {
  const text = input.trim();
  const t = text.toLowerCase();
  if (!t) return { kind: "none" };

  if (/\b(resume|unpause|start monitoring again|turn monitoring back on)\b/.test(t)) {
    return { kind: "tool", tool: "pause_monitoring", args: { hours: 0 } };
  }
  if (/\b(pause|stop monitoring|away|travell?ing|staying with)\b/.test(t)) {
    let hours = 24;
    const h = t.match(/(\d+(?:\.\d+)?)\s*(hours?|hrs?|h)\b/);
    const d = t.match(/(\d+)\s*days?\b/);
    if (h) hours = Number(h[1]);
    else if (d) hours = Number(d[1]) * 24;
    else if (/\b(tonight|today)\b/.test(t)) hours = 12;
    else if (/\bweekend\b/.test(t)) hours = 48;
    return { kind: "tool", tool: "pause_monitoring", args: { hours: Math.min(hours, 336), reason: text } };
  }
  if (/\b(acknowledge|ack|i'?m on it|on my way|got it|handled|i'?ll call|i called|resolved?)\b/.test(t)) {
    return { kind: "tool", tool: "acknowledge_alert", args: { acknowledged_by: "caregiver", note: text } };
  }
  if (/\b(door|porch|front|camera|ring|look outside|see outside)\b/.test(t)) {
    return { kind: "tool", tool: "check_the_door", args: {} };
  }
  if (/\b(visit|carer|caregiver comes|nurse|physio|comes (on|every))\b/.test(t) && /\d/.test(t)) {
    const v = parseVisit(text);
    if (v) return { kind: "tool", tool: "add_expected_visit", args: v };
    return { kind: "help", text: "Tell me who, which days and a time range, e.g. \"Carer visits Tue and Fri 11:00 to 12:00\"." };
  }
  if (/\b(summary|what happened|how was|today's log|day so far|yesterday)\b/.test(t)) {
    const args = {};
    const iso = t.match(/\b(\d{4}-\d{2}-\d{2})\b/);
    if (iso) args.date_iso = iso[1];
    else if (/\byesterday\b/.test(t)) args.date_iso = isoDaysAgo(1);
    return { kind: "tool", tool: "get_day_summary", args };
  }
  if (/\b(check(ed|s)?[- ]?in|is (dad|mum|mom|he|she) ok|okay|status|alerts?|did (dad|mum|mom|he|she))\b/.test(t)) {
    return { kind: "tool", tool: "get_routine_status", args: {} };
  }
  return {
    kind: "help",
    text: "I can answer: \"Did Dad check in today?\", \"Can I see Dad's door?\", \"What happened today?\", "
      + "\"I'm on it\" (acknowledge), \"Pause for 2 days\", \"Carer visits Tue and Fri 11 to 12\".",
  };
}

function isoDaysAgo(n) {
  const d = new Date(Date.now() - n * 86400000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
