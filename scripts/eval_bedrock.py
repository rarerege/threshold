r"""Routing accuracy of the Bedrock router on web/eval_phrases.json (first tool chosen; tools are not run).

Usage: .\.venv\Scripts\python.exe scripts\eval_bedrock.py      (needs BEDROCK_MODEL_ID + AWS settings in .env)
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from core.engine.service import Engine  # noqa: E402
from core.mcp_server.server import build_server  # noqa: E402
from core.orchestrator.bedrock import AssistantUnavailable, BedrockAssistant  # noqa: E402
from core.store import db  # noqa: E402
from core.store.models import Household  # noqa: E402


async def main() -> int:
    load_dotenv(ROOT / ".env", override=True)
    eng = Engine(db.connect(":memory:"))
    eng.ensure_household(Household(id="home"))
    mcp = build_server(eng, "home", snapshot_dir=ROOT / "data" / "eval_snaps")
    tools = [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in await mcp.list_tools()]
    try:
        assistant = BedrockAssistant.from_env(tools)
    except AssistantUnavailable as e:
        print(f"Bedrock not configured: {e.reason}")
        return 2

    phrases = json.loads((ROOT / "web" / "eval_phrases.json").read_text(encoding="utf-8"))
    ok, latencies = 0, []
    for p in phrases:
        t0 = time.time()
        try:
            r = await assistant.ask(None, p["text"])  # fresh conversation per phrase
        except AssistantUnavailable as e:
            print(f"ERROR {p['text']!r}: {e.reason}")
            return 1
        latencies.append(time.time() - t0)
        got = r["name"] if r["type"] == "tool_call" else "none"
        extra = f" input={r['input']}" if r["type"] == "tool_call" and r["input"] else (f" reply={r['text'][:60]!r}" if got == "none" else "")
        passed = got == p["tool"]
        ok += passed
        print(f"{'PASS' if passed else 'FAIL'}  {p['text']:55} expected={p['tool']:20} got={got}{extra}")
    print(f"\nBedrock router ({assistant.model_id}): {ok}/{len(phrases)} ({round(100 * ok / len(phrases))}%), "
          f"median {statistics.median(latencies):.2f}s, max {max(latencies):.2f}s per routing call")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
