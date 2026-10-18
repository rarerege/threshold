"""HTTP endpoint the web client uses to ask Bedrock which MCP tool to call (and to phrase the answer).

The browser stays the MCP client: /assistant only returns {type: tool_call, name, input}; the page runs
tools/call over MCP itself and posts the result back here for the grounded answer.

  GET  /assistant/status                 -> {available, model_id, region, reason}
  POST /assistant {conversation_id?, text}                                   -> tool_call | reply
  POST /assistant {conversation_id, tool_result: {tool_use_id, text, structured?, is_error?}} -> tool_call | reply
  503  {type: "unavailable", reason, code}  -> the page falls back to its keyword router
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable
from urllib.parse import urlparse

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.orchestrator.bedrock import AssistantUnavailable, BedrockAssistant
from mcp.server.mcpserver import MCPServer

log = logging.getLogger(__name__)

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _local(request: Request) -> bool:
    """Same guard as the MCP transport's DNS-rebinding protection: only local Host/Origin."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    origin = request.headers.get("origin")
    return host in LOCAL_HOSTS and (origin is None or urlparse(origin).hostname in LOCAL_HOSTS)


def add_assistant_routes(mcp: MCPServer, factory: Callable[[list[dict[str, Any]]], BedrockAssistant]) -> None:
    state: dict[str, Any] = {"assistant": None, "reason": "not tried yet", "code": "not_configured"}

    async def assistant() -> BedrockAssistant:
        if state["assistant"] is None:
            tools = [{"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                     for t in await mcp.list_tools()]
            try:
                state["assistant"] = factory(tools)
            except AssistantUnavailable as e:
                state["reason"], state["code"] = e.reason, e.code
                raise
        return state["assistant"]

    def unavailable(e: AssistantUnavailable) -> JSONResponse:
        return JSONResponse({"type": "unavailable", "reason": e.reason, "code": e.code}, status_code=503)

    @mcp.custom_route("/assistant/status", methods=["GET"], include_in_schema=False)
    async def status(request: Request) -> Response:
        if not _local(request):
            return Response(status_code=403)
        try:
            a = await assistant()
            return JSONResponse({"available": True, "model_id": a.model_id,
                                 "region": os.environ.get("AWS_REGION", ""), "reason": ""})
        except AssistantUnavailable as e:
            return JSONResponse({"available": False, "model_id": None, "region": os.environ.get("AWS_REGION", ""),
                                 "reason": e.reason})

    @mcp.custom_route("/assistant", methods=["POST"], include_in_schema=False)
    async def ask(request: Request) -> Response:
        if not _local(request):
            return Response(status_code=403)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except ValueError as e:
            return JSONResponse({"type": "error", "reason": f"bad request: {e}"}, status_code=400)
        try:
            a = await assistant()
            if "tool_result" in body:
                tr = body["tool_result"] or {}
                result = await a.tool_result(str(body.get("conversation_id") or ""), str(tr.get("tool_use_id") or ""),
                                             text=str(tr.get("text") or ""), structured=tr.get("structured"),
                                             is_error=bool(tr.get("is_error")))
            else:
                result = await a.ask(body.get("conversation_id"), str(body.get("text") or ""))
            return JSONResponse(result)
        except AssistantUnavailable as e:
            log.warning("Bedrock unavailable: %s", e.reason)
            return unavailable(e)
        except ValueError as e:
            return JSONResponse({"type": "error", "reason": str(e)}, status_code=400)
