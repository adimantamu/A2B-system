"""MCP over Streamable HTTP — the wire a real chat client connects to.

`server.py` holds the tools. This module is the protocol between them and a
connector registered in someone's Claude or ChatGPT: JSON-RPC 2.0, the
`initialize` handshake, `tools/list` and `tools/call`.

It exists because `/mcp/tools` and `/mcp/call` are our own JSON shape. That
shape is fine for the console's preview and for an in-page bridge, but no MCP
client can reach it — a connector speaks JSON-RPC or it does not connect at all.
This is the gap between "we have tools" and "their AI can run our workflow".

Three decisions worth keeping.

**The MCP session carries the Lenni session.** `initialize` mints an
`Mcp-Session-Id`, the client echoes it on every later request, and the
`ToolContext` hangs off it. So once `lenni_start_session` hands back a session
id we remember it here, and the model never has to thread that id through the
calls that follow. Models forget identifiers halfway through a long interview;
this makes forgetting harmless instead of fatal.

**Tool failures are results, not protocol errors.** A JSON-RPC error means the
request itself was malformed and the model can do nothing useful with it. A tool
that ran and refused returns `isError` with the reason and the hint in the text,
because the model is meant to read that and adapt — which is exactly what
`ToolError` was built to carry.

**Instructions ship with the handshake.** `initialize` returns the operating
rules, so the workflow is enforced by the server on every connection rather than
by whatever text happened to be in the launch link. That is the whole point of
moving off a prompt that carried the interview inside it.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from ..__init__ import __version__
from ..config import GUARDRAIL
from .server import ToolContext, ToolError, call_tool, list_tools

# Revisions we can speak. Tools are identical across all of them, so the list is
# about handshake compatibility rather than behaviour. A client asking for
# something newer gets our latest and decides for itself whether to continue.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL = PROTOCOL_VERSIONS[0]

SERVER_NAME = "lenni-a2b"

# Shown to the model when the connector attaches. This is where the workflow is
# imposed: the vendor owns the questions, the server owns the order, and the
# model narrates. Keep it short — every word here is spent on every session.
INSTRUCTIONS = f"""\
Lenni runs a business's own intake workflow. The business wrote the sections and
the questions; your job is to conduct them, not to design them.

Always start with lenni_start_session. It returns the first section: an opening
line, the questions to ask, and what to capture. Ask that section's questions in
its wording and in its order. Do not invent questions, reorder them, skip ahead,
or summarise several sections at once.

When the customer has answered a section, call lenni_complete_section with what
you captured. It returns the next section. Repeat until it reports done, then
call lenni_finish to assemble and deliver the package.

lenni_validate_package tells you what is still missing at any point.
lenni_get_section re-reads the section in progress if you lose your place.

{GUARDRAIL}
"""

# How long an idle connection keeps its remembered session id. Long enough for a
# customer to break off mid-interview and come back after lunch, short enough
# that an abandoned handshake does not sit in memory for a day.
SESSION_TTL_SECONDS = 6 * 60 * 60


@dataclass
class _Connection:
    ctx: ToolContext = field(default_factory=ToolContext)
    touched: float = field(default_factory=time.time)


_CONNECTIONS: dict[str, _Connection] = {}


@dataclass
class Reply:
    """What the HTTP layer should send back.

    `body` of None means send no body at all, which is what a JSON-RPC
    notification requires — answering one with a JSON-RPC response is a protocol
    error, not a harmless extra.
    """
    status: int
    body: Optional[dict] = None
    session_id: Optional[str] = None


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def required_token() -> str:
    return (os.environ.get("A2B_MCP_TOKEN") or "").strip()


def authorized(authorization_header: Optional[str]) -> bool:
    """Bearer check, skipped entirely when no token is configured.

    An unset `A2B_MCP_TOKEN` leaves the endpoint open, which is the right
    default for a local demo and the wrong one for anything holding real
    customer data. Deployments set the variable; the tests do not.
    """
    token = required_token()
    if not token:
        return True
    header = (authorization_header or "").strip()
    if not header.lower().startswith("bearer "):
        return False
    return header[7:].strip() == token


# --------------------------------------------------------------------------
# JSON-RPC envelopes
# --------------------------------------------------------------------------

def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def parse_error() -> dict:
    return _error(None, -32700, "Parse error: body is not valid JSON.")


# --------------------------------------------------------------------------
# Connection state
# --------------------------------------------------------------------------

def _purge() -> None:
    cutoff = time.time() - SESSION_TTL_SECONDS
    for sid in [s for s, c in _CONNECTIONS.items() if c.touched < cutoff]:
        _CONNECTIONS.pop(sid, None)


def _open_connection() -> str:
    _purge()
    sid = uuid.uuid4().hex
    _CONNECTIONS[sid] = _Connection()
    return sid


def close_connection(session_id: Optional[str]) -> None:
    if session_id:
        _CONNECTIONS.pop(session_id, None)


def _context(session_id: Optional[str]) -> ToolContext:
    """The context for this request, remembered across calls where possible.

    A client that never sends the session header still works; it just gets a
    fresh context each time and has to pass `session_id` explicitly, which is
    what the tool schemas ask for anyway.
    """
    conn = _CONNECTIONS.get(session_id or "")
    if conn is None:
        return ToolContext()
    conn.touched = time.time()
    return conn.ctx


# --------------------------------------------------------------------------
# Method handlers
# --------------------------------------------------------------------------

def _initialize(msg_id: Any, params: dict) -> Reply:
    requested = params.get("protocolVersion")
    version = requested if requested in PROTOCOL_VERSIONS else LATEST_PROTOCOL
    sid = _open_connection()
    return Reply(
        200,
        _result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": INSTRUCTIONS,
        }),
        session_id=sid,
    )


def _tools_list(msg_id: Any) -> Reply:
    return Reply(200, _result(msg_id, {"tools": list_tools()}))


def _tool_failure_text(exc: ToolError) -> str:
    parts = [exc.message]
    if exc.hint:
        parts.append(exc.hint)
    return f"{exc.code}: " + " ".join(parts)


def _tools_call(msg_id: Any, params: dict, session_id: Optional[str]) -> Reply:
    name = params.get("name")
    if not name:
        return Reply(200, _error(msg_id, -32602, "tools/call requires a tool name."))

    ctx = _context(session_id)
    arguments = params.get("arguments") or {}
    try:
        result = call_tool(name, arguments, ctx)
    except ToolError as exc:
        return Reply(200, _result(msg_id, {
            "content": [{"type": "text", "text": _tool_failure_text(exc)}],
            "isError": True,
        }))

    # Remember the session the moment one is minted, so the rest of the
    # interview does not depend on the model echoing the id back to us.
    started = result.get("session_id") if isinstance(result, dict) else None
    if started:
        ctx.session_id = started

    return Reply(200, _result(msg_id, {
        "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
        "isError": False,
    }))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def handle(message: Any, *, session_id: Optional[str] = None) -> Reply:
    """Route one JSON-RPC message.

    Batches are not supported: they were removed from the specification, and
    accepting them here would mean pretending to a capability we do not
    advertise in the handshake.
    """
    if isinstance(message, list):
        return Reply(400, _error(None, -32600, "Batched requests are not supported."))
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return Reply(400, _error(None, -32600, "Invalid Request: expected JSON-RPC 2.0."))

    method = message.get("method")
    params = message.get("params") or {}
    msg_id = message.get("id")

    # No id means a notification. The specification is explicit that these get
    # an accepted status and nothing else, so `initialized` must not be answered.
    if "id" not in message:
        return Reply(202)

    if method == "initialize":
        return _initialize(msg_id, params)
    if method == "ping":
        return Reply(200, _result(msg_id, {}))
    if method == "tools/list":
        return _tools_list(msg_id)
    if method == "tools/call":
        return _tools_call(msg_id, params, session_id)

    return Reply(200, _error(msg_id, -32601, f"Method not found: {method!r}."))
