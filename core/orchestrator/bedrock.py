"""Bedrock Converse router: speech/text -> one MCP tool call -> a short answer grounded in its result.

The model only *routes* and *phrases*. The browser executes every tool through MCP (tools/call), and
the deterministic engine decides every alert. Guardrails here are code, not prompt:
  * only the MCP tools are offered, and unknown tool names are refused;
  * `confirm` is removed from pause_monitoring's schema and stripped from any input, so only the
    caregiver's click in the page can confirm a pause;
  * snapshots never reach the model (tool results are sent as text/JSON only);
  * at most `max_tool_calls` tool calls per question;
  * every clock time in the model's answer must appear in the tool result, otherwise the tool's own
    text is returned instead.

Field names follow the botocore bedrock-runtime service model (Converse, 2023-09-30).
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

STRIPPED_INPUTS = {"pause_monitoring": {"confirm"}}
MAX_TEXT = 500
MAX_RESULT_CHARS = 20_000
TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")

SYSTEM_PROMPT = """You are Threshold, a calm assistant that helps an adult child keep an eye on their parent ({parent}), who lives alone.
Rules:
- For any question about {parent}'s check-in, alerts, day, front door, expected visits or monitoring, call exactly one tool. Never answer those from memory.
- After a tool result, reply in one or two short spoken sentences using only facts in that result. Copy times exactly as written. Do not add facts.
- You never see images. Never describe, identify or guess who is at the door.
- Never call or promise to call emergency services. If {parent} needs help, tell the caregiver to contact {parent} directly.
- pause_monitoring only takes effect after the caregiver presses Confirm in the app. Never say monitoring is paused unless the tool result's status is "paused".
- Use acknowledge_alert only when the caregiver clearly says they are handling an alert.
- If the request is unrelated, say in one sentence what you can help with, without calling a tool."""


class AssistantUnavailable(Exception):
    """Bedrock can't be used right now (not configured, no access, throttled, timed out...)."""

    def __init__(self, reason: str, code: str = "unavailable"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


def tool_specs(mcp_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Converse toolSpec list from MCP tools/list entries (name, description, inputSchema)."""
    specs = []
    for t in mcp_tools:
        schema = copy.deepcopy(t.get("inputSchema") or {"type": "object", "properties": {}})
        for prop in STRIPPED_INPUTS.get(t["name"], ()):
            schema.get("properties", {}).pop(prop, None)
            if prop in schema.get("required", []):
                schema["required"].remove(prop)
        specs.append({"toolSpec": {"name": t["name"], "description": (t.get("description") or t["name"])[:1000],
                                   "inputSchema": {"json": schema}}})
    return specs


def grounded(reply: str, evidence: str) -> bool:
    """Deterministic check: every HH:MM the model mentions must appear in the tool result."""
    return all(m.group(0) in evidence for m in TIME_RE.finditer(reply))


@dataclass
class Conversation:
    id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    updated: float = field(default_factory=time.time)
    tool_calls_this_turn: int = 0
    pending: list[dict[str, Any]] = field(default_factory=list)  # toolUse blocks awaiting results
    last_result_text: str = ""
    evidence: str = ""


class BedrockAssistant:
    def __init__(
        self,
        client: Any,
        model_id: str,
        mcp_tools: list[dict[str, Any]],
        *,
        parent_name: str = "Dad",
        max_tool_calls: int = 2,
        max_conversations: int = 200,
        ttl_s: float = 1800,
        clock: Callable[[], float] = time.time,
    ):
        self.client = client
        self.model_id = model_id
        self.tools = tool_specs(mcp_tools)
        self.allowed = {t["toolSpec"]["name"] for t in self.tools}
        self.system = [{"text": SYSTEM_PROMPT.format(parent=parent_name)}]
        self.max_tool_calls = max_tool_calls
        self.max_conversations = max_conversations
        self.ttl_s = ttl_s
        self.clock = clock
        self.conversations: dict[str, Conversation] = {}

    @classmethod
    def from_env(cls, mcp_tools: list[dict[str, Any]], **kwargs: Any) -> "BedrockAssistant":
        """Build from BEDROCK_MODEL_ID / AWS_REGION and the standard AWS credential chain."""
        model_id = os.environ.get("BEDROCK_MODEL_ID", "").strip()
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        if not model_id:
            raise AssistantUnavailable("BEDROCK_MODEL_ID is not set", "not_configured")
        if not region:
            raise AssistantUnavailable("AWS_REGION is not set", "not_configured")
        import boto3
        from botocore.config import Config

        session = boto3.Session(region_name=region)
        if session.get_credentials() is None:
            raise AssistantUnavailable("no AWS credentials found", "not_configured")
        client = session.client("bedrock-runtime", config=Config(
            connect_timeout=3, read_timeout=8, retries={"max_attempts": 2, "mode": "standard"}))
        return cls(client, model_id, mcp_tools, **kwargs)

    # --- conversation bookkeeping ---------------------------------------------

    def _conversation(self, conv_id: str | None) -> Conversation:
        now = self.clock()
        for k in [k for k, c in self.conversations.items() if now - c.updated > self.ttl_s]:
            del self.conversations[k]
        conv = self.conversations.get(conv_id or "")
        if conv is None:
            if len(self.conversations) >= self.max_conversations:
                oldest = min(self.conversations.values(), key=lambda c: c.updated)
                del self.conversations[oldest.id]
            conv = Conversation(id=uuid.uuid4().hex)
            self.conversations[conv.id] = conv
        conv.updated = now
        return conv

    @staticmethod
    def _add_user(conv: Conversation, blocks: list[dict[str, Any]]) -> None:
        """Converse needs alternating roles, so consecutive user content is merged into one message."""
        if conv.messages and conv.messages[-1]["role"] == "user":
            conv.messages[-1]["content"].extend(blocks)
        else:
            conv.messages.append({"role": "user", "content": blocks})

    @staticmethod
    def _trim(conv: Conversation, keep: int = 16) -> None:
        """Keep recent history, cutting only where a plain user text message starts."""
        if len(conv.messages) <= keep:
            return
        for i in range(len(conv.messages) - keep, len(conv.messages)):
            m = conv.messages[i]
            if m["role"] == "user" and all("text" in b for b in m["content"]):
                conv.messages = conv.messages[i:]
                return

    # --- public API -------------------------------------------------------------

    async def ask(self, conv_id: str | None, text: str) -> dict[str, Any]:
        text = (text or "").strip()[:MAX_TEXT]
        if not text:
            raise ValueError("text is empty")
        conv = self._conversation(conv_id)
        if conv.pending:  # a previous tool call was abandoned (e.g. page reloaded): close it out
            self._add_user(conv, [self._result_block(t["toolUseId"], "error", text="Not run; the user asked something else.")
                                  for t in conv.pending])
            conv.pending = []
        conv.tool_calls_this_turn = 0
        conv.last_result_text = ""
        conv.evidence = ""
        self._add_user(conv, [{"text": text}])
        self._trim(conv)
        return await self._step(conv)

    async def tool_result(self, conv_id: str, tool_use_id: str, *, text: str = "",
                          structured: dict[str, Any] | None = None, is_error: bool = False) -> dict[str, Any]:
        conv = self.conversations.get(conv_id)
        if conv is None or not conv.pending or conv.pending[0]["toolUseId"] != tool_use_id:
            raise ValueError("no pending tool call with that id")
        first, rest = conv.pending[0], conv.pending[1:]
        conv.pending = []
        payload = json.dumps(structured, ensure_ascii=False) if structured is not None else ""
        if len(payload) > MAX_RESULT_CHARS:
            structured, payload = None, ""
        blocks = [self._result_block(first["toolUseId"], "error" if is_error else "success",
                                     text=text, structured=structured)]
        blocks += [self._result_block(t["toolUseId"], "error", text="Only one tool runs per step.") for t in rest]
        self._add_user(conv, blocks)
        conv.last_result_text = text
        conv.evidence = f"{text}\n{payload}"
        return await self._step(conv)

    # --- internals ----------------------------------------------------------------

    @staticmethod
    def _result_block(tool_use_id: str, status: str, *, text: str = "",
                      structured: dict[str, Any] | None = None) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if structured is not None:
            content.append({"json": structured})
        if text or not content:
            content.append({"text": text or "(no output)"})
        return {"toolResult": {"toolUseId": tool_use_id, "content": content, "status": status}}

    async def _converse(self, conv: Conversation) -> dict[str, Any]:
        from botocore.exceptions import BotoCoreError, ClientError

        request = {
            "modelId": self.model_id,
            "messages": conv.messages,
            "system": self.system,
            "toolConfig": {"tools": self.tools},  # toolChoice omitted: auto is the default and not every model accepts it
            "inferenceConfig": {"maxTokens": 300, "temperature": 0},
        }
        try:
            return await asyncio.to_thread(lambda: self.client.converse(**request))
        except ClientError as e:
            err = e.response.get("Error", {})
            raise AssistantUnavailable(f"{err.get('Code', 'ClientError')}: {err.get('Message', '')}"[:300],
                                       err.get("Code", "client_error")) from e
        except BotoCoreError as e:
            raise AssistantUnavailable(f"{type(e).__name__}: {e}"[:300], type(e).__name__) from e

    async def _step(self, conv: Conversation) -> dict[str, Any]:
        resp = await self._converse(conv)
        msg = resp["output"]["message"]
        conv.messages.append({"role": "assistant", "content": msg["content"]})
        meta = {"conversation_id": conv.id, "model_id": self.model_id, "stop_reason": resp["stopReason"],
                "latency_ms": resp.get("metrics", {}).get("latencyMs"), "usage": resp.get("usage", {})}

        uses = [b["toolUse"] for b in msg["content"] if "toolUse" in b]
        if resp["stopReason"] == "tool_use" and uses:
            if conv.tool_calls_this_turn >= self.max_tool_calls:
                self._add_user(conv, [self._result_block(u["toolUseId"], "error", text="Tool limit reached.") for u in uses])
                return {**meta, "type": "reply", "text": conv.last_result_text or "Sorry, I couldn't finish that.",
                        "source": "tool_text", "note": "tool call limit reached"}
            allowed = [u for u in uses if u["name"] in self.allowed]
            refused = [u for u in uses if u["name"] not in self.allowed]
            if refused:
                log.warning("model asked for unknown tools: %s", [u["name"] for u in refused])
            if not allowed:
                self._add_user(conv, [self._result_block(u["toolUseId"], "error", text="Unknown tool.") for u in refused])
                conv.tool_calls_this_turn += 1
                return await self._step(conv)
            conv.pending = allowed + refused
            conv.tool_calls_this_turn += 1
            call = allowed[0]
            tool_input = {k: v for k, v in (call.get("input") or {}).items()
                          if k not in STRIPPED_INPUTS.get(call["name"], set())}
            return {**meta, "type": "tool_call", "tool_use_id": call["toolUseId"], "name": call["name"], "input": tool_input}

        text = " ".join(b["text"] for b in msg["content"] if "text" in b).strip()
        if conv.evidence and (not text or not grounded(text, conv.evidence)):
            log.warning("ungrounded or empty model reply replaced with tool text: %r", text[:200])
            return {**meta, "type": "reply", "text": conv.last_result_text, "source": "tool_text",
                    "note": "model reply failed the grounding check"}
        if not text:
            text = "Sorry, I didn't catch that. You can ask whether Dad checked in, or to see the door."
        return {**meta, "type": "reply", "text": text, "source": "model"}
