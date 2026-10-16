// Run: node --test web/
import assert from "node:assert/strict";
import { test } from "node:test";
import { parseVisit, route } from "./router.js";

const tool = (s) => route(s).tool;

test("definition-of-done phrases", () => {
  assert.equal(tool("Can I see Dad's door?"), "check_the_door");
  assert.equal(tool("Did Dad check in today?"), "get_routine_status");
});

test("other read phrases", () => {
  assert.equal(tool("Is Dad ok?"), "get_routine_status");
  assert.equal(tool("Any alerts?"), "get_routine_status");
  assert.equal(tool("What happened today?"), "get_day_summary");
  assert.equal(route("summary for 2026-10-15").args.date_iso, "2026-10-15");
  assert.equal(tool("Show me the front porch"), "check_the_door");
});

test("acknowledge", () => {
  const r = route("I'm on it, calling him now");
  assert.equal(r.tool, "acknowledge_alert");
  assert.equal(r.args.note, "I'm on it, calling him now");
});

test("pause durations and resume", () => {
  assert.deepEqual(route("Pause monitoring for 6 hours").args.hours, 6);
  assert.deepEqual(route("Dad is staying with us for 3 days, pause").args.hours, 72);
  assert.deepEqual(route("pause for the weekend").args.hours, 48);
  assert.equal(route("pause").args.hours, 24);
  assert.equal(route("pause for 30 days").args.hours, 336);
  assert.deepEqual(route("resume monitoring").args, { hours: 0 });
  assert.equal(route("pause for 2 days").args.confirm, undefined); // the page asks the user before confirm=true
});

test("visits", () => {
  assert.deepEqual(parseVisit("Carer visits Tue and Fri 11:00 to 12:00"),
    { label: "Carer", start: "11:00", end: "12:00", days: ["tue", "fri"] });
  assert.deepEqual(parseVisit("The physio comes every Monday 3 to 4pm"),
    { label: "Physio", start: "15:00", end: "16:00", days: ["mon"] });
  assert.deepEqual(parseVisit("nurse visits weekdays 9am to 9:30am"),
    { label: "Nurse", start: "09:00", end: "09:30", days: ["mon", "tue", "wed", "thu", "fri"] });
  assert.equal(tool("Carer visits Tue and Fri 11 to 12"), "add_expected_visit");
  assert.equal(route("carer visits sometimes 11").kind, "help");
});

test("unknown input gets help, not a guess", () => {
  assert.equal(route("what's the weather").kind, "help");
  assert.equal(route("   ").kind, "none");
});
