r"""Bedrock gate: which Claude models can this account call, and does Converse tool use work?

Usage: .\.venv\Scripts\python.exe scripts\bedrock_gate.py [--model <modelId or inference profile id>]

1. Lists Anthropic foundation models and system-defined inference profiles in AWS_REGION.
2. Tries Converse with one real Threshold tool (get_routine_status) on --model, or BEDROCK_MODEL_ID,
   or else each candidate (smaller models first) until one works. AccessDenied etc. are reported, not hidden.
3. Sends a toolResult back and prints the grounded answer, stopReason, latency and token usage.
Secrets are never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import boto3  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from core.orchestrator.bedrock import SYSTEM_PROMPT  # noqa: E402

TOOL = {"toolSpec": {"name": "get_routine_status",
                     "description": "Did the parent check in today? Returns today's check-in state and open alerts.",
                     "inputSchema": {"json": {"type": "object", "properties": {}}}}}
SIZE_ORDER = ("haiku", "sonnet", "opus")


def rank(model_id: str) -> int:
    return next((i for i, s in enumerate(SIZE_ORDER) if s in model_id.lower()), len(SIZE_ORDER))


def err(e: Exception) -> str:
    if isinstance(e, ClientError):
        x = e.response.get("Error", {})
        return f"{x.get('Code')}: {x.get('Message')}"
    return f"{type(e).__name__}: {e}"


def try_converse(rt, model_id: str) -> bool:
    print(f"\n== Converse with {model_id}")
    messages = [{"role": "user", "content": [{"text": "Did Dad check in today?"}]}]
    base = {"modelId": model_id, "system": [{"text": SYSTEM_PROMPT.format(parent="Dad")}],
            "toolConfig": {"tools": [TOOL]}, "inferenceConfig": {"maxTokens": 200, "temperature": 0}}
    t0 = time.time()
    try:
        r1 = rt.converse(messages=messages, **base)
    except (ClientError, BotoCoreError) as e:
        print(f"   FAILED: {err(e)}")
        return False
    uses = [b["toolUse"] for b in r1["output"]["message"]["content"] if "toolUse" in b]
    print(f"   turn 1: stopReason={r1['stopReason']} toolUse={[u['name'] for u in uses]} "
          f"latencyMs={r1['metrics']['latencyMs']} wall={time.time() - t0:.2f}s usage={r1['usage']}")
    if r1["stopReason"] != "tool_use" or not uses:
        print("   (model answered without calling the tool)")
        return True
    messages += [{"role": "assistant", "content": r1["output"]["message"]["content"]},
                 {"role": "user", "content": [{"toolResult": {"toolUseId": uses[0]["toolUseId"], "status": "success", "content": [
                     {"json": {"routine_state": "checked_in", "last_checkin": {"time_local": "08:15", "status": "ok"}}},
                     {"text": "Yes. Dad checked in at 08:15 and said they're ok."}]}}]}]
    t1 = time.time()
    try:
        r2 = rt.converse(messages=messages, **base)
    except (ClientError, BotoCoreError) as e:
        print(f"   turn 2 FAILED: {err(e)}")
        return False
    text = " ".join(b["text"] for b in r2["output"]["message"]["content"] if "text" in b)
    print(f"   turn 2: stopReason={r2['stopReason']} latencyMs={r2['metrics']['latencyMs']} "
          f"wall={time.time() - t1:.2f}s usage={r2['usage']}")
    print(f"   answer: {text!r}")
    return True


def main() -> int:
    load_dotenv(ROOT / ".env", override=True)
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=os.environ.get("BEDROCK_MODEL_ID") or None)
    args = p.parse_args()

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    session = boto3.Session(region_name=region)
    creds = session.get_credentials()
    print(f"region={region} credentials={'found via ' + creds.method if creds else 'NOT FOUND'}")
    if not region or not creds:
        print("Set AWS_REGION and AWS keys (or AWS_PROFILE) in .env first.")
        return 2

    bedrock = session.client("bedrock")
    candidates: list[str] = []
    try:
        models = [m for m in bedrock.list_foundation_models()["modelSummaries"]
                  if (m.get("providerName") or "").lower() == "anthropic"]
        print(f"\n== Anthropic foundation models in {region}: {len(models)}")
        for m in sorted(models, key=lambda m: m["modelId"]):
            status = (m.get("modelLifecycle") or {}).get("status")
            print(f"   {m['modelId']:55} inference={m.get('inferenceTypesSupported')} status={status} "
                  f"tools-capable-text={'TEXT' in m.get('outputModalities', [])}")
            if "ON_DEMAND" in (m.get("inferenceTypesSupported") or []) and status == "ACTIVE":
                candidates.append(m["modelId"])
    except (ClientError, BotoCoreError) as e:
        print(f"   list_foundation_models FAILED: {err(e)}")
    try:
        profiles = []
        for page in bedrock.get_paginator("list_inference_profiles").paginate(typeEquals="SYSTEM_DEFINED"):
            profiles += [ip for ip in page["inferenceProfileSummaries"] if "anthropic" in ip["inferenceProfileId"]]
        print(f"\n== Anthropic system inference profiles: {len(profiles)}")
        for ip in sorted(profiles, key=lambda ip: ip["inferenceProfileId"]):
            print(f"   {ip['inferenceProfileId']:55} status={ip.get('status')}")
            if ip.get("status") == "ACTIVE":
                candidates.append(ip["inferenceProfileId"])
    except (ClientError, BotoCoreError) as e:
        print(f"   list_inference_profiles FAILED: {err(e)}")

    rt = session.client("bedrock-runtime")
    order = [args.model] if args.model else sorted(dict.fromkeys(candidates), key=lambda m: (rank(m), m))
    if not order:
        print("\nNo candidate models found. Check Bedrock model access for Anthropic in this region.")
        return 1
    for model_id in order[:6]:
        if try_converse(rt, model_id):
            print(f"\nPASS. Add to .env:\nBEDROCK_MODEL_ID={model_id}")
            return 0
    print("\nNo model accepted the Converse call; see the errors above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
