// Routing accuracy of the keyword router on web/eval_phrases.json.  Usage: node scripts/eval_keywords.mjs
import { readFileSync } from "node:fs";
import { route } from "../web/router.js";

const phrases = JSON.parse(readFileSync(new URL("../web/eval_phrases.json", import.meta.url), "utf8"));
let ok = 0;
for (const p of phrases) {
  const r = route(p.text);
  const got = r.kind === "tool" ? r.tool : "none";
  const pass = got === p.tool;
  if (pass) ok++;
  console.log(`${pass ? "PASS" : "FAIL"}  ${p.text.padEnd(55)} expected=${p.tool.padEnd(20)} got=${got}`);
}
console.log(`\nkeyword router: ${ok}/${phrases.length} (${Math.round((100 * ok) / phrases.length)}%)`);
