"""The connector transport — what a real Claude or ChatGPT actually speaks.

The console's `/mcp/tools` and `/mcp/call` are our own shape and no MCP client
can reach them, which is why a URL in a prompt never worked. These tests cover
the JSON-RPC surface that does: the handshake, discovery, invocation, and the
one behaviour the whole interview depends on — that a session survives across
calls without the model having to carry the id.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from a2b import skills
from a2b.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def vendor_slug(published_skill, vendor_id):
    return skills.get_vendor(vendor_id)["slug"]


def rpc(client, method, params=None, *, msg_id=1, session=None, headers=None):
    body = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        body["id"] = msg_id
    if params is not None:
        body["params"] = params
    hdrs = dict(headers or {})
    if session:
        hdrs["Mcp-Session-Id"] = session
    return client.post("/mcp", json=body, headers=hdrs)


def open_session(client):
    r = rpc(client, "initialize", {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    })
    return r, r.headers.get("mcp-session-id")


def tool_result(response):
    """Unwrap the JSON a tool returned inside the MCP text content block."""
    payload = response.json()["result"]
    assert payload["isError"] is False
    return json.loads(payload["content"][0]["text"])


# --------------------------------------------------------------------------
# Handshake
# --------------------------------------------------------------------------

def test_initialize_returns_a_usable_handshake(client):
    r, session = open_session(client)
    result = r.json()["result"]

    assert r.status_code == 200
    assert result["protocolVersion"] == "2025-06-18"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "lenni-a2b"
    assert session


def test_an_unknown_protocol_version_gets_ours_rather_than_a_refusal(client):
    r = rpc(client, "initialize", {"protocolVersion": "1999-01-01", "capabilities": {}})
    assert r.json()["result"]["protocolVersion"] == "2025-06-18"


def test_the_handshake_carries_the_workflow_rules(client):
    """The instructions are how the server owns the interview. Without them the
    order of the sections would once again depend on whatever text happened to
    be in the launch link."""
    r, _ = open_session(client)
    instructions = r.json()["result"]["instructions"]

    assert "lenni_start_session" in instructions
    assert "lenni_complete_section" in instructions
    assert "not a rate quote" in instructions


def test_notifications_get_no_response_body(client):
    """Answering a notification with a JSON-RPC response is a protocol error,
    not a harmless extra."""
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202
    assert r.content == b""


def test_batches_are_refused_rather_than_half_handled(client):
    r = client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32600


def test_a_malformed_body_is_a_parse_error(client):
    r = client.post("/mcp", content=b"{not json",
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == -32700


def test_an_unknown_method_is_reported_not_ignored(client):
    r = rpc(client, "resources/list")
    assert r.json()["error"]["code"] == -32601


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def test_tools_list_exposes_the_section_walk(client):
    _, session = open_session(client)
    tools = {t["name"]: t for t in rpc(client, "tools/list", session=session).json()["result"]["tools"]}

    assert {"lenni_start_session", "lenni_complete_section", "lenni_finish"} <= set(tools)
    assert tools["lenni_start_session"]["inputSchema"]["required"] == ["vendor", "skill"]


def test_read_only_tools_say_so_and_writing_ones_do_not(client):
    _, session = open_session(client)
    tools = {t["name"]: t for t in rpc(client, "tools/list", session=session).json()["result"]["tools"]}

    assert tools["lenni_validate_package"]["annotations"]["readOnlyHint"] is True
    assert "annotations" not in tools["lenni_complete_section"]


# --------------------------------------------------------------------------
# Running the workflow
# --------------------------------------------------------------------------

def test_a_customer_can_run_a_whole_skill_over_the_connector(client, vendor_slug):
    _, session = open_session(client)

    started = tool_result(rpc(client, "tools/call", {
        "name": "lenni_start_session",
        "arguments": {"vendor": vendor_slug, "skill": "multifamily-perm",
                      "email": "borrower@example.com", "terminal": "claude"},
    }, session=session))

    assert started["section"]["name"] == "Collateral"
    section = started["section"]

    while True:
        step = tool_result(rpc(client, "tools/call", {
            "name": "lenni_complete_section",
            "arguments": {"section_id": section["section_id"],
                          "facts": {f"{section['name'].lower()}_note": "captured"}},
        }, session=session))
        if step["done"]:
            break
        section = step["section"]

    finished = tool_result(rpc(client, "tools/call",
                               {"name": "lenni_finish", "arguments": {}}, session=session))
    assert finished


def test_the_session_survives_without_the_model_repeating_the_id(client, vendor_slug):
    """The reason the handshake mints a session at all. A model that forgets the
    id mid-interview used to strand the customer; now the connection remembers."""
    _, session = open_session(client)

    rpc(client, "tools/call", {
        "name": "lenni_start_session",
        "arguments": {"vendor": vendor_slug, "skill": "multifamily-perm"},
    }, session=session)

    # No session_id anywhere in the arguments.
    current = tool_result(rpc(client, "tools/call",
                              {"name": "lenni_get_section", "arguments": {}}, session=session))
    assert current["section"]["name"] == "Collateral"


def test_a_client_that_ignores_the_session_header_still_works_explicitly(client, vendor_slug):
    started = tool_result(rpc(client, "tools/call", {
        "name": "lenni_start_session",
        "arguments": {"vendor": vendor_slug, "skill": "multifamily-perm"},
    }))
    current = tool_result(rpc(client, "tools/call", {
        "name": "lenni_get_section",
        "arguments": {"session_id": started["session_id"]},
    }))
    assert current["section"]["name"] == "Collateral"


def test_a_refused_tool_is_a_result_the_model_can_read(client):
    """Not a JSON-RPC error. The model is supposed to see the reason and adapt,
    and a transport-level error gives it nothing to work with."""
    _, session = open_session(client)
    payload = rpc(client, "tools/call", {
        "name": "lenni_get_section", "arguments": {},
    }, session=session).json()["result"]

    assert payload["isError"] is True
    text = payload["content"][0]["text"]
    assert "no_session" in text
    assert "lenni_start_session" in text


def test_an_unknown_tool_lists_what_is_available(client):
    _, session = open_session(client)
    payload = rpc(client, "tools/call",
                  {"name": "nope", "arguments": {}}, session=session).json()["result"]

    assert payload["isError"] is True
    assert "lenni_start_session" in payload["content"][0]["text"]


# --------------------------------------------------------------------------
# The GET path, which serves two different callers
# --------------------------------------------------------------------------

def test_a_client_opening_an_event_stream_is_told_to_post(client):
    r = client.get("/mcp", headers={"Accept": "text/event-stream"})
    assert r.status_code == 405
    assert "POST" in r.headers["allow"]


def test_a_browser_looking_the_endpoint_up_gets_an_explanation(client):
    body = client.get("/mcp").json()
    assert body["transport"] == "streamable-http"
    assert "2025-06-18" in body["protocol_versions"]


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def test_no_token_configured_leaves_the_endpoint_open(client, monkeypatch):
    monkeypatch.delenv("A2B_MCP_TOKEN", raising=False)
    assert rpc(client, "ping").status_code == 200


def test_a_configured_token_is_enforced(client, monkeypatch):
    monkeypatch.setenv("A2B_MCP_TOKEN", "s3cret")

    assert rpc(client, "ping").status_code == 401
    assert rpc(client, "ping", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert rpc(client, "ping", headers={"Authorization": "Bearer s3cret"}).status_code == 200
