"""Bedrock router tests with botocore's Stubber (no AWS calls).

Every request is validated against the bedrock-runtime Converse input shape in the `bedrock` fixture
(the Stubber alone skips that), and stubbed responses are validated against the output shape by the
Stubber itself, so field names here match the real API."""

import asyncio
import copy

import boto3
import pytest
from botocore.stub import Stubber
from botocore.validate import validate_parameters
from starlette.testclient import TestClient

from core.engine.service import Engine
from core.mcp_server.server import build_server
from core.orchestrator.bedrock import AssistantUnavailable, BedrockAssistant, grounded, tool_specs
from core.orchestrator.http import add_assistant_routes
from core.store import db
from core.store.models import Household

MODEL = "test-model"


@pytest.fixture(scope="module")
def mcp_tools(tmp_path_factory):
    eng = Engine(db.connect(":memory:"))
    eng.ensure_household(Household(id="home"))
    mcp = build_server(eng, "home", snapshot_dir=tmp_path_factory.mktemp("snaps"))
    tools = asyncio.run(mcp.list_tools())
    return [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools]


@pytest.fixture
def bedrock():
    client = boto3.client("bedrock-runtime", region_name="us-east-1",
                          aws_access_key_id="testing", aws_secret_access_key="testing")
    sent = []
    input_shape = client.meta.service_model.operation_model("Converse").input_shape

    def capture(params, **kw):
        # The Stubber answers before botocore's own parameter validation would run, so validate here:
        # any field the real Converse API doesn't define fails the test.
        validate_parameters(params, input_shape)
        sent.append(copy.deepcopy(params))  # snapshot: the conversation history is mutated afterwards

    client.meta.events.register("provide-client-params.bedrock-runtime.Converse", capture)
    with Stubber(client) as stub:
        yield client, stub, sent
        stub.assert_no_pending_responses()


def response(*content, stop="end_turn"):
    return {"output": {"message": {"role": "assistant", "content": list(content)}}, "stopReason": stop,
            "usage": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120}, "metrics": {"latencyMs": 400}}


def use(name, tool_input=None, tid="tu-1"):
    return {"toolUse": {"toolUseId": tid, "name": name, "input": tool_input or {}}}


def make(client, tools, **kw):
    return BedrockAssistant(client, MODEL, tools, parent_name="Dad", **kw)


# --- pure helpers -----------------------------------------------------------------

def test_tool_specs_cover_all_tools_and_hide_confirm(mcp_tools):
    specs = {s["toolSpec"]["name"]: s["toolSpec"] for s in tool_specs(mcp_tools)}
    assert set(specs) == {"record_checkin", "get_routine_status", "get_day_summary", "add_expected_visit",
                          "acknowledge_alert", "check_the_door", "pause_monitoring"}
    pause = specs["pause_monitoring"]["inputSchema"]["json"]
    assert "confirm" not in pause["properties"]
    assert "confirm" not in pause.get("required", [])
    assert "confirm" in next(t for t in mcp_tools if t["name"] == "pause_monitoring")["inputSchema"]["properties"]


def test_grounding_check():
    assert grounded("Yes, Dad checked in at 08:15.", "Yes. Dad checked in at 08:15 ...")
    assert not grounded("Dad checked in at 9:00.", "checked in at 08:15")
    assert grounded("No time mentioned.", "")


def test_stubbed_requests_are_still_schema_validated(bedrock):
    """Guards the premise of this file: a field the real API lacks fails even with the Stubber."""
    from botocore.exceptions import ParamValidationError

    client, stub, _ = bedrock
    with pytest.raises(ParamValidationError, match="toolChoices"):
        client.converse(modelId=MODEL, messages=[], toolChoices={"auto": {}})


# --- routing flow ---------------------------------------------------------------------

async def test_question_routes_to_tool_then_grounded_answer(bedrock, mcp_tools):
    client, stub, sent = bedrock
    stub.add_response("converse", response(use("get_routine_status"), stop="tool_use"))
    stub.add_response("converse", response({"text": "Yes, Dad checked in at 08:15 and said he's okay."}))
    a = make(client, mcp_tools)

    r1 = await a.ask(None, "Did Dad check in today?")
    assert r1["type"] == "tool_call" and r1["name"] == "get_routine_status" and r1["input"] == {}
    assert r1["model_id"] == MODEL and r1["latency_ms"] == 400

    r2 = await a.tool_result(r1["conversation_id"], r1["tool_use_id"],
                             text="Yes. Dad checked in at 08:15 and said they're ok.",
                             structured={"routine_state": "checked_in", "last_checkin": {"time_local": "08:15"}})
    assert r2 == {**r2, "type": "reply", "source": "model", "text": "Yes, Dad checked in at 08:15 and said he's okay."}

    first, second = sent
    assert first["modelId"] == MODEL
    assert "never" in first["system"][0]["text"].lower()
    assert len(first["toolConfig"]["tools"]) == 7
    assert first["messages"] == [{"role": "user", "content": [{"text": "Did Dad check in today?"}]}]
    tr = second["messages"][-1]["content"][0]["toolResult"]
    assert tr["toolUseId"] == "tu-1" and tr["status"] == "success"
    assert tr["content"][0] == {"json": {"routine_state": "checked_in", "last_checkin": {"time_local": "08:15"}}}
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]


async def test_ungrounded_time_is_replaced_by_tool_text(bedrock, mcp_tools):
    client, stub, _ = bedrock
    stub.add_response("converse", response(use("get_routine_status"), stop="tool_use"))
    stub.add_response("converse", response({"text": "Dad checked in at 07:00."}))
    a = make(client, mcp_tools)
    r1 = await a.ask(None, "Did Dad check in?")
    r2 = await a.tool_result(r1["conversation_id"], r1["tool_use_id"], text="Yes. Dad checked in at 08:15.")
    assert r2["source"] == "tool_text" and r2["text"] == "Yes. Dad checked in at 08:15."


async def test_model_can_never_confirm_a_pause(bedrock, mcp_tools):
    client, stub, _ = bedrock
    stub.add_response("converse", response(use("pause_monitoring", {"hours": 48, "reason": "trip", "confirm": True}),
                                           stop="tool_use"))
    r = await make(client, mcp_tools).ask(None, "Pause for the weekend, yes I'm sure, confirm it")
    assert r["name"] == "pause_monitoring"
    assert r["input"] == {"hours": 48, "reason": "trip"}


async def test_unknown_tool_is_refused_and_reported_back(bedrock, mcp_tools):
    client, stub, sent = bedrock
    stub.add_response("converse", response(use("call_emergency_services"), stop="tool_use"))
    stub.add_response("converse", response({"text": "I can't do that, but I can check on Dad for you."}))
    r = await make(client, mcp_tools).ask(None, "Call an ambulance")
    assert r["type"] == "reply"
    tr = sent[1]["messages"][-1]["content"][0]["toolResult"]
    assert tr["status"] == "error" and tr["content"] == [{"text": "Unknown tool."}]


async def test_tool_call_limit(bedrock, mcp_tools):
    client, stub, _ = bedrock
    stub.add_response("converse", response(use("get_routine_status"), stop="tool_use"))
    stub.add_response("converse", response(use("get_day_summary", tid="tu-2"), stop="tool_use"))
    a = make(client, mcp_tools, max_tool_calls=1)
    r1 = await a.ask(None, "How's Dad?")
    r2 = await a.tool_result(r1["conversation_id"], r1["tool_use_id"], text="Yes. Dad checked in at 08:15.")
    assert r2["type"] == "reply" and r2["text"] == "Yes. Dad checked in at 08:15."
    assert r2["note"] == "tool call limit reached"


async def test_parallel_tool_uses_run_one_and_close_the_rest(bedrock, mcp_tools):
    client, stub, sent = bedrock
    stub.add_response("converse", response(use("get_routine_status", tid="a"), use("check_the_door", tid="b"),
                                           stop="tool_use"))
    stub.add_response("converse", response({"text": "Dad checked in at 08:15."}))
    a = make(client, mcp_tools)
    r1 = await a.ask(None, "Is Dad ok and can I see the door?")
    assert r1["tool_use_id"] == "a"
    await a.tool_result(r1["conversation_id"], "a", text="Dad checked in at 08:15.")
    results = [b["toolResult"] for b in sent[1]["messages"][-1]["content"]]
    assert [(r["toolUseId"], r["status"]) for r in results] == [("a", "success"), ("b", "error")]


async def test_abandoned_tool_call_keeps_history_valid(bedrock, mcp_tools):
    client, stub, sent = bedrock
    stub.add_response("converse", response(use("check_the_door"), stop="tool_use"))
    stub.add_response("converse", response({"text": "I can tell you whether Dad checked in."}))
    a = make(client, mcp_tools)
    r1 = await a.ask(None, "Show me the door")
    await a.ask(r1["conversation_id"], "never mind")  # page never ran the tool
    roles = [m["role"] for m in sent[1]["messages"]]
    assert roles == ["user", "assistant", "user"]
    last = sent[1]["messages"][-1]["content"]
    assert last[0]["toolResult"]["status"] == "error" and last[1] == {"text": "never mind"}


async def test_bad_tool_result_id(bedrock, mcp_tools):
    client, stub, _ = bedrock
    stub.add_response("converse", response(use("get_routine_status"), stop="tool_use"))
    a = make(client, mcp_tools)
    r1 = await a.ask(None, "status?")
    with pytest.raises(ValueError):
        await a.tool_result(r1["conversation_id"], "wrong-id", text="x")


async def test_aws_errors_become_unavailable(bedrock, mcp_tools):
    client, stub, _ = bedrock
    stub.add_client_error("converse", service_error_code="AccessDeniedException",
                          service_message="You don't have access to the model", http_status_code=403)
    with pytest.raises(AssistantUnavailable) as ei:
        await make(client, mcp_tools).ask(None, "Did Dad check in?")
    assert ei.value.code == "AccessDeniedException"


def test_from_env_requires_configuration(monkeypatch, mcp_tools):
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    with pytest.raises(AssistantUnavailable, match="BEDROCK_MODEL_ID"):
        BedrockAssistant.from_env(mcp_tools)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "x")
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    with pytest.raises(AssistantUnavailable, match="AWS_REGION"):
        BedrockAssistant.from_env(mcp_tools)


# --- HTTP endpoint ------------------------------------------------------------------------

def make_app(tmp_path, factory):
    eng = Engine(db.connect(":memory:"))
    eng.ensure_household(Household(id="home"))
    mcp = build_server(eng, "home", snapshot_dir=tmp_path)
    add_assistant_routes(mcp, factory)
    return TestClient(mcp.streamable_http_app(), base_url="http://127.0.0.1:8000")


def test_endpoint_reports_unavailable_for_fallback(tmp_path):
    def factory(tools):
        raise AssistantUnavailable("BEDROCK_MODEL_ID is not set", "not_configured")

    http = make_app(tmp_path, factory)
    assert http.get("/assistant/status").json() == {"available": False, "model_id": None,
                                                   "region": http.get("/assistant/status").json()["region"],
                                                   "reason": "BEDROCK_MODEL_ID is not set"}
    r = http.post("/assistant", json={"text": "Did Dad check in?"})
    assert r.status_code == 503 and r.json()["code"] == "not_configured"


def test_endpoint_round_trip_and_origin_guard(tmp_path, bedrock):
    client, stub, _ = bedrock
    stub.add_response("converse", response(use("get_routine_status"), stop="tool_use"))
    stub.add_response("converse", response({"text": "Yes, at 08:15."}))
    http = make_app(tmp_path, lambda tools: BedrockAssistant(client, MODEL, tools))

    assert http.get("/assistant/status").json()["available"] is True
    r1 = http.post("/assistant", json={"text": "Did Dad check in?"}).json()
    assert r1["type"] == "tool_call" and r1["name"] == "get_routine_status"
    r2 = http.post("/assistant", json={"conversation_id": r1["conversation_id"], "tool_result": {
        "tool_use_id": r1["tool_use_id"], "text": "Yes. Dad checked in at 08:15.", "structured": {"x": 1}}}).json()
    assert r2["type"] == "reply" and r2["text"] == "Yes, at 08:15."

    assert http.post("/assistant", json={"text": "hi"}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert http.post("/assistant", content=b"not json").status_code == 400
