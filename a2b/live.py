"""Live start links: one fixed address that always carries today's prompt.

The demo workspace compiles a product's entry prompt in the browser and, until
this, kept it there. A bank that pasted the start buttons onto its own site got a
copy of the prompt frozen into every link, so an edit in the workspace never
reached the page a customer clicks.

These rows are the server's copy. The workspace sends the compiled prompt when a
change alters it, and `/go/{vendor}/{product}/{terminal}` looks it up at the
moment of the click, so the address pasted onto the bank's page never has to
change.

Keyed by slug rather than vendor id because the workspace's banks live in its
fixture, not in the `vendor` table. Who may write a row is decided in
`guard.live_editor`.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import quote

from .config import LIVE_BASE
from .db import cursor, dumps, loads, new_id, now
from .links import MAX_QUERY, TERMINALS, short_prompt

STATUSES = ("published", "unpublished")
TERMINAL_KEYS = tuple(t.key for t in TERMINALS)

# What the workspace and the console both produce. These segments are echoed
# into a page and into a redirect, so anything else is refused at the door.
_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,78}[a-z0-9])?$")

# Ten times the longest intake we have seen compile, and small enough that the
# table cannot be used as file storage. What a link can carry is a separate
# limit, handled by `pack_for_url` and `MAX_REDIRECT_QUERY`.
MAX_PROMPT = 50_000
MAX_NOTICE = 2_000
MAX_NAME = 200
MAX_PAGE = 64_000

# The redirect carries the prompt in its Location header, and every hop between
# here and the browser caps how large a response header may be. Packing keeps a
# normal intake under MAX_QUERY; this is the ceiling for the case where even a
# packed prompt is too long, past which the link points at the text instead.
MAX_REDIRECT_QUERY = 16_000


def valid_slug(value: str) -> bool:
    return bool(_SLUG.match(value or ""))


def page_url(vendor_slug: str, product_slug: str, *, fmt: str = "") -> str:
    suffix = f".{fmt}" if fmt else ""
    return f"{LIVE_BASE}/go/{vendor_slug}/{product_slug}{suffix}"


def links(vendor_slug: str, product_slug: str) -> dict:
    """The addresses a bank pastes. None of them ever changes."""
    return {
        "page": page_url(vendor_slug, product_slug),
        "workflow": page_url(vendor_slug, product_slug, fmt="md"),
        "terminals": {
            t.key: f"{LIVE_BASE}/go/{vendor_slug}/{product_slug}/{t.key}"
            for t in TERMINALS if t.supported and t.template
        },
    }


# --------------------------------------------------------------------------
# Fitting a prompt into a link, the way the workspace does
# --------------------------------------------------------------------------
#
# A click on /go/ has to hand the assistant the same text the workspace's own
# start buttons would, so this is a line-for-line port of `packForUrl` in the
# demo's app.js. The regular expressions spell out what JavaScript means by
# `^`, `.` and `$` under the `m` flag, because Python's defaults differ on the
# rarer line terminators.

_JS_SPACE = "\t\n\x0b\x0c\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006" \
            "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def _js_line(label: str) -> re.Pattern:
    """`/^ {2}<label> .*(?:\\n|$)/gm`"""
    return re.compile(
        r"(?:\A|(?<=[\n\r\u2028\u2029])) {2}" + re.escape(label)
        + r"[^\n\r\u2028\u2029]*(?:\n|(?=[\r\u2028\u2029])|\Z)"
    )


_COLLECT = _js_line("Collect: ")
_OPEN_WITH = _js_line("Open with: ")
_INTAKE_END = "\n--- end of "
_CONTINUES = "\n(Intake continues — ask me for the rest.)"

# The characters encodeURIComponent leaves alone. Measuring with the same
# encoding means a prompt that fits in the workspace fits here.
_URI_COMPONENT_SAFE = "-_.!~*'()"


def encode_component(text: str) -> str:
    return quote(text, safe=_URI_COMPONENT_SAFE)


def _fits(text: str) -> bool:
    return len(encode_component(text)) <= MAX_QUERY


def _js_slice(text: str, units: int) -> str:
    """`text.slice(0, units)`, counting UTF-16 code units as JavaScript does.

    Where that would split a pair the browser's link throws instead, so the
    half character is dropped.
    """
    used = 0
    for i, ch in enumerate(text):
        used += 2 if ord(ch) > 0xFFFF else 1
        if used > units:
            return text[:i]
    return text


def pack_for_url(text: str) -> str:
    """Shorten a prompt to fit a `?q=` link without losing a question.

    Sheds the "Collect:" lines first, then the "Open with:" lines, and only then
    whole sections from the end of the intake, keeping everything after the end
    marker: the pacing rules, the guardrail and the closing message.
    """
    t = (text or "").strip(_JS_SPACE)
    if not t or _fits(t):
        return t

    s = _COLLECT.sub("", t)
    if _fits(s):
        return s

    s = _OPEN_WITH.sub("", s)
    if _fits(s):
        return s

    end = s.find(_INTAKE_END)
    if end > 0:
        tail = s[end:]
        room = MAX_QUERY - len(encode_component(tail + _CONTINUES))
        head = s[:end]
        while head and len(encode_component(head)) > room:
            cut = head.rfind("\n\nSection ")
            if cut <= 0:
                break
            head = head[:cut]
        return head + _CONTINUES + tail
    return _js_slice(s, MAX_QUERY // 2)


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------

def get(vendor_slug: str, product_slug: str) -> Optional[dict]:
    with cursor() as cur:
        row = cur.execute(
            "SELECT * FROM live_prompt WHERE vendor_slug=? AND product_slug=?",
            (vendor_slug, product_slug),
        ).fetchone()
    if not row:
        return None
    out = dict(row)
    out["page"] = loads(out.pop("page_json"), {})
    return out


def for_vendor(vendor_slug: str) -> list[dict]:
    """Every product with a live row under one organisation, by slug."""
    with cursor() as cur:
        rows = cur.execute(
            "SELECT vendor_slug, product_slug, status, version, updated_at"
            " FROM live_prompt WHERE vendor_slug=? ORDER BY product_slug",
            (vendor_slug,),
        ).fetchall()
    return [dict(r) for r in rows]


def _sendable(text: str) -> bool:
    # A lone surrogate survives JSON but not UTF-8, so it would fail at the
    # click, where nobody is watching, instead of at the save.
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _check(vendor_slug: str, product_slug: str, vendor_name: str, product_name: str,
           status: str, prompt: str, notice: str, page_json: str) -> None:
    if not (valid_slug(vendor_slug) and valid_slug(product_slug)):
        raise ValueError("Slugs are lowercase letters, digits and hyphens.")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if not vendor_name or not product_name:
        raise ValueError("vendor_name and product_name are required.")
    if len(vendor_name) > MAX_NAME or len(product_name) > MAX_NAME:
        raise ValueError(f"Names are limited to {MAX_NAME} characters.")
    if status == "published" and not prompt.strip():
        raise ValueError("A published product needs a prompt.")
    if len(prompt) > MAX_PROMPT:
        raise ValueError(f"The prompt is limited to {MAX_PROMPT:,} characters.")
    if len(notice) > MAX_NOTICE:
        raise ValueError(f"The notice is limited to {MAX_NOTICE:,} characters.")
    if len(page_json) > MAX_PAGE:
        raise ValueError(f"The page details are limited to {MAX_PAGE:,} characters.")
    if not all(_sendable(v) for v in (vendor_name, product_name, prompt, notice, page_json)):
        raise ValueError("The text contains a character that cannot be sent in a link.")


def put(vendor_slug: str, product_slug: str, *, vendor_name: str, product_name: str,
        status: str, actor_email: str, prompt: str = "", notice: str = "",
        page: Optional[dict] = None) -> dict:
    """Store what the workspace just published, and log the change.

    A write that changes nothing is not recorded. Publishing an offering that is
    already live sends the same text again, and a log where most rows are
    identical would bury the edits that matter.

    Returns the stored row with `changed` saying whether this call altered it.
    """
    vendor_name = (vendor_name or "").strip()
    product_name = (product_name or "").strip()
    prompt = prompt or ""
    notice = (notice or "").strip()
    page_json = dumps(page or {})
    _check(vendor_slug, product_slug, vendor_name, product_name, status, prompt, notice,
           page_json)
    # An unpublished product has nothing to hand out. Keeping the old prompt in
    # the row would leave it one bug away from being served.
    if status == "unpublished":
        prompt = ""

    current = get(vendor_slug, product_slug)
    if current and (
        current["vendor_name"], current["product_name"], current["status"],
        current["prompt"], current["notice"], dumps(current["page"]),
    ) == (vendor_name, product_name, status, prompt, notice, page_json):
        return {**current, "changed": False}

    stamp = now()
    actor = (actor_email or "").strip().lower()
    with cursor() as cur:
        # The version is bumped in the same statement that writes the row, so two
        # saves racing from different workers cannot both claim the same number.
        cur.execute(
            "INSERT INTO live_prompt (vendor_slug, product_slug, vendor_name, product_name,"
            " status, prompt, notice, page_json, version, updated_by, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,1,?,?)"
            " ON CONFLICT(vendor_slug, product_slug) DO UPDATE SET"
            " vendor_name=excluded.vendor_name, product_name=excluded.product_name,"
            " status=excluded.status, prompt=excluded.prompt, notice=excluded.notice,"
            " page_json=excluded.page_json, version=live_prompt.version + 1,"
            " updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (vendor_slug, product_slug, vendor_name, product_name, status, prompt, notice,
             page_json, actor, stamp),
        )
        version = cur.execute(
            "SELECT version FROM live_prompt WHERE vendor_slug=? AND product_slug=?",
            (vendor_slug, product_slug),
        ).fetchone()["version"]
        cur.execute(
            "INSERT INTO live_prompt_event (id, vendor_slug, product_slug, version, status,"
            " prompt, prompt_sha256, actor_email, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (new_id("lpe"), vendor_slug, product_slug, version, status, prompt,
             hashlib.sha256(prompt.encode("utf-8")).hexdigest(), actor, stamp),
        )
    return {**get(vendor_slug, product_slug), "changed": True}


def history(vendor_slug: str, product_slug: str, limit: int = 50) -> list[dict]:
    """Newest change first."""
    with cursor() as cur:
        rows = cur.execute(
            "SELECT version, status, prompt, prompt_sha256, actor_email, created_at"
            " FROM live_prompt_event WHERE vendor_slug=? AND product_slug=?"
            " ORDER BY version DESC LIMIT ?",
            (vendor_slug, product_slug, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def public_view(row: dict) -> dict:
    """What anyone may read.

    The prompt is public by design, since it rides in every link, but only while
    the product is published. Who made the last change is not public.
    """
    published = row["status"] == "published"
    return {
        "vendor": {"slug": row["vendor_slug"], "name": row["vendor_name"]},
        "product": {"slug": row["product_slug"], "name": row["product_name"]},
        "status": row["status"],
        "prompt": row["prompt"] if published else "",
        "notice": row["notice"],
        "page": row["page"],
        "version": row["version"],
        "updated_at": row["updated_at"],
        "links": links(row["vendor_slug"], row["product_slug"]),
    }


def destination(row: dict, terminal_key: str) -> Optional[str]:
    """Where a click on this assistant goes right now.

    None when there is nowhere to send the customer with a prompt: the product is
    unpublished, or the assistant no longer accepts a prompt from a link.
    """
    if row["status"] != "published" or not row["prompt"]:
        return None
    terminal = next((t for t in TERMINALS if t.key == terminal_key), None)
    if not terminal or not (terminal.supported and terminal.template):
        return None
    query = encode_component(pack_for_url(row["prompt"]))
    if len(query) > MAX_REDIRECT_QUERY:
        workflow = page_url(row["vendor_slug"], row["product_slug"], fmt="md")
        query = encode_component(short_prompt(workflow))
    return terminal.template.format(prompt=query)
