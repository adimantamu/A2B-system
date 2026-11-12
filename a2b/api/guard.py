"""Per-route access checks.

These are plain functions called at the top of a handler rather than FastAPI
`Depends`, for one reason: the vendor a request touches is not always a path
parameter. Sometimes it is `/api/vendors/{vendor_id}`, sometimes it has to be
looked up from a skill or a section id. Making each handler say which object it
is guarding, and at what role, keeps the answer to "who can call this?" on the
same screen as the route.

The default minimum is `editor`, not `viewer`, because every guarded route that
is not explicitly a read is a write.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request

from .. import auth
from ..config import live_vendors


def _fail(exc: auth.AuthError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def current_user(request: Request) -> Optional[dict]:
    """The signed-in user, or None. Never raises — for routes that render
    differently when signed in but do not require it."""
    return auth.user_for_token(request.cookies.get(auth.COOKIE_NAME))


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to continue.")
    return user


def staff(request: Request) -> dict:
    """Assert the caller is us. Returns the user.

    404 rather than 403, and the same 404 a signed-out caller gets, because the
    honest answer — "this exists and you are not allowed" — tells someone
    probing that there is an internal portal here and that getting an address
    onto a list is what stands between them and every bank's data. A vendor who
    stumbles onto the URL sees a page that is not there.
    """
    user = current_user(request)
    if not user or not auth.is_staff(user["email"]):
        raise HTTPException(404, "Not found.")
    return user


def vendor(request: Request, vendor_id: str, minimum: str = "editor") -> dict:
    """Assert the caller may act on this vendor. Returns {user, role}."""
    user = require_user(request)
    try:
        role = auth.require_role(user["email"], vendor_id, minimum)
    except auth.AuthError as exc:
        raise _fail(exc) from exc
    return {"user": user, "role": role}


def skill(request: Request, skill_id: str, minimum: str = "editor") -> dict:
    """Same, for a skill. Returns {user, role, vendor_id}."""
    user = require_user(request)
    vendor_id = auth.vendor_for_skill(skill_id)
    if not vendor_id:
        raise HTTPException(404, "no such skill")
    try:
        role = auth.require_role(user["email"], vendor_id, minimum)
    except auth.AuthError as exc:
        # The skill exists but not for this caller. Say the same thing as if it
        # did not exist, so ids cannot be probed.
        raise HTTPException(404, "no such skill") from exc
    return {"user": user, "role": role, "vendor_id": vendor_id}


def live_editor(request: Request, vendor_slug: str) -> dict:
    """Assert the caller may change a bank's live prompts. Returns {user, role}.

    Staff may write any real vendor, and the demo organisations named in
    A2B_LIVE_VENDORS, which are fixture data with no `vendor` row to hold a
    membership. Anyone else needs editor on the real vendor with that slug.
    Signup is open, so "signed in" alone would let a stranger put a prompt of
    their choosing behind a lenni.io link carrying a bank's name.

    A named demo organisation stays staff-only even where a real vendor shares
    its slug, as the seeded demo bank does: those rows mirror the workspace's
    fixture, and a membership in the real vendor says nothing about the demo.
    """
    user = require_user(request)
    vendor_id = auth.vendor_for_slug(vendor_slug)
    if auth.is_staff(user["email"]):
        if vendor_id or vendor_slug in live_vendors():
            return {"user": user, "role": "staff"}
        raise HTTPException(403, "Live links are not turned on for this organization.")
    if not vendor_id:
        raise HTTPException(404, "No such vendor.")
    if vendor_slug in live_vendors():
        raise HTTPException(403, "Only Lenni staff may change this organization's live links.")
    try:
        role = auth.require_role(user["email"], vendor_id, "editor")
    except auth.AuthError as exc:
        raise _fail(exc) from exc
    return {"user": user, "role": role}


def section(request: Request, section_id: str, minimum: str = "editor") -> dict:
    user = require_user(request)
    vendor_id = auth.vendor_for_section(section_id)
    if not vendor_id:
        raise HTTPException(404, "no such section")
    try:
        role = auth.require_role(user["email"], vendor_id, minimum)
    except auth.AuthError as exc:
        raise HTTPException(404, "no such section") from exc
    return {"user": user, "role": role, "vendor_id": vendor_id}
