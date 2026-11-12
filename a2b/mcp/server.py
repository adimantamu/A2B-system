"""The tool surface — how a customer's AI actually runs a skill.

These are transport-agnostic on purpose. `transport.py` exposes them to a
registered connector over JSON-RPC, the console reaches the same functions
through its own JSON preview endpoints, and an in-page bridge could register
them with a browser agent. None of that is visible from here.

Tool names keep the `lenni_` prefix from the existing connector. The brief is
explicit that these are the same tools gaining tenancy, not replacements, so
renaming them would break the one integration already in the wild for no gain.

What is new is that every session-bound tool now resolves through a vendor and a
skill. The old no-vendor path still exists and is the generic marketplace.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .. import sessions
from ..compiler import mcp_url
from ..manifest import agents_page_data
from ..skills import get_skill_by_slug, get_vendor_by_slug, list_skills


class ToolError(Exception):
    """An error a calling model can act on: code, message, and a hint."""

    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "hint": self.hint}}


@dataclass
class ToolContext:
    """Who is calling, and on whose behalf.

    `vendor_slug` being set is what puts the session on a vendor's own machine.
    When it is set, marketplace matching is off — on a bank's own site the
    customer sees that bank's products, not a comparison against its competitors.
    """
    vendor_slug: Optional[str] = None
    email: Optional[str] = None
    session_id: Optional[str] = None

    @property
    def marketplace(self) -> bool:
        return self.vendor_slug is None


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[[dict, ToolContext], dict]
    # Advertised to the caller as `readOnlyHint`. A model that knows a tool
    # cannot change anything will call it freely to orient itself, and a client
    # that gates writes behind confirmation knows which ones to leave alone.
    read_only: bool = False


_TOOLS: dict[str, Tool] = {}


def tool(name: str, description: str, schema: Optional[dict] = None,
         read_only: bool = False):
    def decorator(fn):
        _TOOLS[name] = Tool(name=name, description=description,
                            schema=schema or {"type": "object", "properties": {}},
                            fn=fn, read_only=read_only)
        return fn
    return decorator


def list_tools() -> list[dict]:
    out = []
    for t in _TOOLS.values():
        entry = {"name": t.name, "description": t.description, "inputSchema": t.schema}
        if t.read_only:
            entry["annotations"] = {"readOnlyHint": True}
        out.append(entry)
    return out


def call_tool(name: str, args: dict, ctx: Optional[ToolContext] = None) -> dict:
    ctx = ctx or ToolContext()
    tool_obj = _TOOLS.get(name)
    if not tool_obj:
        raise ToolError("unknown_tool", f"No tool named {name!r}.",
                        hint=f"Available: {', '.join(sorted(_TOOLS))}")
    try:
        return tool_obj.fn(args or {}, ctx)
    except ToolError:
        raise
    except KeyError as exc:
        raise ToolError("not_found", str(exc), hint="Check the identifier and try again.") from exc
    except PermissionError as exc:
        raise ToolError("not_published", str(exc),
                        hint="Only published skills are runnable.") from exc
    except ValueError as exc:
        raise ToolError("invalid_request", str(exc)) from exc


def _require_session(args: dict, ctx: ToolContext) -> str:
    sid = args.get("session_id") or ctx.session_id
    if not sid:
        raise ToolError("no_session", "This tool needs a session_id.",
                        hint="Call lenni_start_session first and keep the id it returns.")
    return sid


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

@tool(
    "lenni_list_services",
    "List the services a vendor publishes, with the section walk for each. "
    "Draft services are never returned.",
    {"type": "object", "properties": {"vendor": {"type": "string"}}, "required": ["vendor"]},
    read_only=True,
)
def _list_services(args: dict, ctx: ToolContext) -> dict:
    slug = args.get("vendor") or ctx.vendor_slug
    vendor = get_vendor_by_slug(slug or "")
    if not vendor:
        raise ToolError("not_found", f"No vendor {slug!r}.",
                        hint="Use the vendor slug from the start link or the agent manifest.")
    data = agents_page_data(vendor["id"])
    return {
        "vendor": data["vendor"],
        "services": [s for s in data["services"] if s["runnable"]],
    }


# --------------------------------------------------------------------------
# Session lifecycle
# --------------------------------------------------------------------------

@tool(
    "lenni_start_session",
    "Begin a run of a vendor's published skill. Returns the first section: what "
    "to say, what to ask, and how many sections follow.",
    {
        "type": "object",
        "properties": {
            "vendor": {"type": "string", "description": "Vendor slug."},
            "skill": {"type": "string", "description": "Skill slug."},
            "email": {"type": "string", "description": "Customer email — keys the portable profile."},
            "terminal": {"type": "string", "description": "claude | chatgpt | grok | copilot"},
        },
        "required": ["vendor", "skill"],
    },
)
def _start_session(args: dict, ctx: ToolContext) -> dict:
    vendor_slug = args.get("vendor") or ctx.vendor_slug
    if not vendor_slug:
        raise ToolError("invalid_request", "A vendor slug is required.")
    session = sessions.start_session(
        vendor_slug=vendor_slug,
        skill_slug=args["skill"],
        email=args.get("email") or ctx.email,
        terminal=args.get("terminal"),
    )
    section = sessions.current_section(session["id"])
    return {
        "session_id": session["id"],
        "connector": mcp_url(vendor_slug, args["skill"]),
        "section": section,
        "guardrail": (
            "Introductory only. Do not state a rate, estimate approval odds, or imply a "
            "decision. The vendor decides everything."
        ),
    }


@tool(
    "lenni_get_section",
    "Return the section currently in progress for a session.",
    {"type": "object", "properties": {"session_id": {"type": "string"}}},
    read_only=True,
)
def _get_section(args: dict, ctx: ToolContext) -> dict:
    sid = _require_session(args, ctx)
    section = sessions.current_section(sid)
    if not section:
        return {"section": None, "done": True,
                "message": "Every section is complete. Call lenni_finish."}
    return {"section": section, "done": False}


@tool(
    "lenni_complete_section",
    "Mark the current section complete and receive the next one. This is the "
    "chaining step — call it when the customer has answered a section's questions.",
    {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "section_id": {"type": "string"},
            "facts": {"type": "object", "description": "What the section captured."},
        },
        "required": ["section_id"],
    },
)
def _complete_section(args: dict, ctx: ToolContext) -> dict:
    sid = _require_session(args, ctx)
    result = sessions.complete_section(sid, args["section_id"], args.get("facts"))
    if result["done"]:
        return {"done": True, "next_step": "Call lenni_finish to assemble and deliver."}
    return {"done": False, "section": result["section"]}


@tool(
    "lenni_save_deal_facts",
    "Persist facts mid-section without advancing. Facts stay with this session.",
    {
        "type": "object",
        "properties": {"session_id": {"type": "string"}, "facts": {"type": "object"}},
        "required": ["facts"],
    },
)
def _save_facts(args: dict, ctx: ToolContext) -> dict:
    from ..db import cursor, dumps, loads

    sid = _require_session(args, ctx)
    session = sessions.get_session(sid)
    if not session:
        raise ToolError("not_found", f"No session {sid!r}.")
    merged = dict(session["facts"])
    merged.update(args.get("facts") or {})
    with cursor() as cur:
        cur.execute("UPDATE session SET facts_json=? WHERE id=?", (dumps(merged), sid))
    # No write through to the profile: the session id proves which run this is,
    # not who the customer is, and the email it was started under was never
    # verified. See `sessions.complete_section` and #24.
    return {"saved": True, "fact_count": len(merged)}


# `lenni_get_user_context` stood here. It took an email and returned that
# person's profile, and it is gone rather than guarded because the guard was the
# problem: any credential we could have asked an arriving customer's AI for is
# one it does not have, and the endpoint has to stay open for that reason.
#
# It is worth being precise about what it leaked, because the second half is
# easy to miss. Reading facts needed a correct guess at an address. Learning
# *whether an address had a profile* needed only the address, since `known` came
# back false for strangers — which turns any mailing list into a check of who
# else banks with whom, one line at a time, without reading a single fact.
#
# The portability it existed for returns with #15, when a customer can prove the
# address is theirs. Until then a session starts empty, which costs a returning
# customer some retyping and costs everyone else nothing.


@tool(
    "lenni_validate_package",
    "Report what is still missing and the next question to ask. Reports gaps; "
    "never judges the deal.",
    {"type": "object", "properties": {"session_id": {"type": "string"}}},
    read_only=True,
)
def _validate(args: dict, ctx: ToolContext) -> dict:
    return sessions.validate_package(_require_session(args, ctx))


@tool(
    "lenni_get_progress",
    "Section-by-section status for a session.",
    {"type": "object", "properties": {"session_id": {"type": "string"}}},
    read_only=True,
)
def _progress(args: dict, ctx: ToolContext) -> dict:
    return sessions.progress(_require_session(args, ctx))


@tool(
    "lenni_finish",
    "Terminal step. Assembles the configured outputs and returns the closing "
    "message plus the secure delivery destination.",
    {"type": "object", "properties": {"session_id": {"type": "string"}}},
)
def _finish(args: dict, ctx: ToolContext) -> dict:
    return sessions.finish(_require_session(args, ctx))


# --------------------------------------------------------------------------
# Marketplace path
# --------------------------------------------------------------------------

@tool(
    "lenni_match_vendors",
    "Marketplace only: rank vendors who actually do this kind of work. Disabled "
    "when the session is running on a vendor's own site.",
    {"type": "object", "properties": {"product_key": {"type": "string"}}},
)
def _match_vendors(args: dict, ctx: ToolContext) -> dict:
    if not ctx.marketplace:
        raise ToolError(
            "disabled_on_vendor_site",
            "Vendor matching is off inside a vendor's own skill.",
            hint="On a vendor's site the customer works with that vendor's products only.",
        )
    # The FFIEC ranking from the borrower build lands here as the fdic-bank-data
    # app. Until that is ported, be honest rather than returning a placebo.
    raise ToolError(
        "not_implemented",
        "Vendor matching is not wired up in this build.",
        hint="Port lenni_match_banks from the FFIEC repository as the fdic-bank-data app.",
    )
