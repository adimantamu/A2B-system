"""FastAPI application: vendor console API, public manifest, and the MCP endpoint.

Three audiences, deliberately separated:

* `/api/*` — the vendor console. Authoring surface.
* `/{vendor}/llms.txt`, `/llms.md`, `/agents`, `/start/{slug}` — the public doors.
  These are what rule 4 calls the two publish outputs.
* `/mcp` — the MCP endpoint a customer's AI connects to as a registered connector.

Only the first of those three is authenticated. The public doors are public by
definition, and the MCP endpoint is reached by a stranger's AI on behalf of a
customer who has no account here — its scoping comes from the session token in
the tool arguments, not from a cookie. Everything under `/api` that reads or
writes tenant state requires a signed-in user with a role on that vendor; see
`guard.py`.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from html import escape as _esc
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import analytics, auth, authpages, live
from .. import ingest as ingest_mod
from .. import mailer
from .. import manifest as manifest_mod
from .. import prospect, registry, sessions, skills
from ..__init__ import __version__
from ..compiler import (compile_final_prompt, compile_full_entry_prompt,
                        compile_short_entry_prompt, delivery_warnings, can_publish,
                        entry_prompt_defaults, render_section, render_workflow)
from ..compiler import (PDF_OFFER, SECTION_CHECK, SUMMARY_OFFER,
                        WORK_FROM_THE_PAGE, destination_label)
from ..config import (ADMIN_DIR, CONSOLE_DIR, CONTENT_DIR, GUARDRAIL, MCP_BASE, PUBLIC_BASE,
                      PUBLIC_DIR)
from ..db import init_db
from ..links import TERMINALS, hosted_start_url, section_url, start_links
from ..mcp import transport
from ..mcp.server import ToolContext, ToolError, call_tool, list_tools
from . import guard


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    auth.purge_expired_sessions()
    # Spent and expired reset tokens, and throttle rows past their window.
    # Nothing reads either after that point and both tables only grow.
    auth.purge_expired_resets()
    auth.purge_old_attempts()
    # Entry prompts are compiled artefacts that embed both the vendor's wording
    # and this deployment's hostname. Rebuilding them here means a restart after
    # a config change, a code change or a new tunnel address cannot leave stale
    # prompts being handed to customers.
    skills.recompile_all()
    yield


app = FastAPI(title="Lenni A2B", version=__version__, lifespan=lifespan)
# Credentials cannot be sent with `allow_origins=["*"]`, and the console is
# served from the same origin as the API, so it does not need to. A third-party
# page therefore cannot make an authenticated call with the user's cookie.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_caching_for_agent_docs(request: Request, call_next):
    """Agent docs are generated per request and must never be served from a cache.

    Two things break when they are. A vendor edits a question and the next
    customer's assistant is handed yesterday's copy by an intermediary, which is
    exactly the promise the hosted docs exist to keep. And the read log stops
    being evidence: a cached hit never reaches us, so a fetch that happened looks
    identical to one that did not.

    We saw both. An assistant produced the bank's exact wording during a run where
    the server recorded no request for it.
    """
    response = await call_next(request)
    path = request.url.path
    if path.endswith((".md", ".txt")) and not path.startswith("/console"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


# --------------------------------------------------------------------------
# Request bodies
# --------------------------------------------------------------------------

class VendorIn(BaseModel):
    name: str
    industry_key: str
    website: str = ""
    description: str = ""


class ScanIn(BaseModel):
    url: str
    vendor_id: Optional[str] = None
    html: Optional[str] = None  # lets the console and tests skip the network


class ConfirmIn(BaseModel):
    vendor_id: str
    match_ids: list[str]
    industry_key: Optional[str] = None


class ProspectIn(BaseModel):
    url: str
    # Capped rather than free: this spends requests on someone else's server.
    max_pages: Optional[int] = None
    # Known to the caller when the scan came from the bundled shortlist.
    name: Optional[str] = None


class SectionIn(BaseModel):
    name: Optional[str] = None
    summary: Optional[str] = None
    says: Optional[str] = None
    asks: Optional[list[str]] = None
    apps: Optional[list[str]] = None


class LibraryAddIn(BaseModel):
    industry_key: str
    index: int


class DestinationIn(BaseModel):
    outputs: Optional[dict] = None
    mode: Optional[str] = None
    connected: Optional[bool] = None
    link: Optional[str] = None
    email: Optional[str] = None
    texts: Optional[dict] = None


class PromptTextsIn(BaseModel):
    """The paragraphs of the entry prompt a vendor may write themselves.

    Spelled out as fields rather than taken as a free dict so the editable seam
    is declared in one place and visible in the API docs. The questions and the
    guardrail are absent because they are not editable from here.
    """
    fresh: Optional[str] = None
    pacing: Optional[str] = None
    section_check: Optional[str] = None
    summary: Optional[str] = None
    pdf: Optional[str] = None


class ToolCallIn(BaseModel):
    name: str
    arguments: dict = {}
    vendor: Optional[str] = None
    email: Optional[str] = None
    session_id: Optional[str] = None


class SignupIn(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str
    password: str


class ProfileIn(BaseModel):
    name: str


class MemberIn(BaseModel):
    email: str
    role: str = "editor"


class LivePromptIn(BaseModel):
    """What the demo workspace sends after a save or a publish.

    `prompt` is the compiled entry prompt, exactly as the workspace would have
    put it into a link. `page` is the rest of its public start payload, kept so a
    start page on another device can show the same thing.
    """
    status: str
    vendor_name: str
    product_name: str
    prompt: str = ""
    notice: str = ""
    page: dict = {}


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.COOKIE_NAME, token,
        httponly=True,               # JavaScript must not be able to read it
        samesite="lax",              # blocks the cookie on cross-site POSTs
        secure=auth.cookie_is_secure(),
        max_age=60 * 60 * 24 * 14,
        path="/",
    )


def _client_ip(request: Request) -> str:
    """The caller's address, as best we can tell.

    Production sits behind Cloudflare, so `request.client` is the proxy and the
    real address arrives in a header. Only these two are trusted, and only
    because nothing reaches the app except through the proxy that sets them; if
    the app is ever exposed directly, a caller could forge either and the
    per-IP throttles would become decorative.
    """
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _me(user: dict) -> dict:
    # `staff` is what the portal boots on. It is not a secret — a staff member
    # already knows what they are, and the flag grants nothing on its own; every
    # internal route re-checks the address against the configured list.
    return {"user": user, "vendors": auth.vendors_for(user["email"]),
            "staff": auth.is_staff(user["email"])}


@app.post("/api/auth/signup")
def api_signup(body: SignupIn, request: Request, response: Response) -> dict:
    """Create an account and sign in.

    This still answers 409 for an address that already has an account, which
    does let a caller learn whether an address is on the system. The generic
    alternative — always answer "check your email" — only works if signup stops
    signing you in, and the funnel here is a prospect who has just scanned
    their own website and needs to land in the workspace holding that scan. An
    email round trip in the middle loses it.
    
    So: the leak is accepted, and bounded by the per-IP throttle below rather
    than left open. Closing it properly means email verification, which is a
    product decision about onboarding, not a fix to make here.
    """
    ip = _client_ip(request)
    try:
        auth.check_rate("signup:ip", ip)
        auth.record_attempt("signup:ip", ip)
        uid = auth.create_user(body.email, body.password, body.name)
    except auth.AuthError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    _set_cookie(response, auth.start_session(uid))
    return _me(auth.get_user(uid))


@app.post("/api/auth/login")
def api_login(body: LoginIn, request: Request, response: Response) -> dict:
    ip = _client_ip(request)
    email = (body.email or "").strip().lower()
    try:
        # Checked before the password is verified so a bucket that is already
        # over its limit costs an attacker a cheap 429 rather than 600k rounds
        # of PBKDF2 on our CPU.
        auth.check_rate("login:email", email)
        auth.check_rate("login:ip", ip)
        user = auth.authenticate(body.email, body.password)
    except auth.AuthError as exc:
        # Only failures count. Recording successes too would throttle a person
        # who simply uses the product a lot from more than one browser.
        if exc.status != 429:
            auth.record_attempt("login:email", email)
            auth.record_attempt("login:ip", ip)
        raise HTTPException(exc.status, exc.message) from exc
    auth.clear_attempts("login:email", email)
    token = auth.start_session(user["id"], request.headers.get("user-agent", ""))
    _set_cookie(response, token)
    return _me(user)


@app.post("/api/auth/forgot")
def api_forgot(body: ForgotIn, request: Request) -> dict:
    """Start a password reset.

    Always answers the same thing. An address with an account, an address
    without one, and an address that was invited but never signed up are
    indistinguishable from the outside — otherwise this endpoint becomes the
    cheapest way to enumerate which people at a bank are on the system.

    Throttled on both axes: per address so it cannot be used to bury someone's
    inbox, per IP so it cannot be used to sweep a list of addresses.
    """
    ip = _client_ip(request)
    email = (body.email or "").strip().lower()
    answer = {"ok": True,
              "message": "If that address has an account, a reset link is on its way."}
    try:
        auth.check_rate("forgot:email", email)
        auth.check_rate("forgot:ip", ip)
    except auth.AuthError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    auth.record_attempt("forgot:email", email)
    auth.record_attempt("forgot:ip", ip)

    issued = auth.begin_password_reset(email, ip)
    if issued:
        _user, token = issued
        mailer.send_password_reset(email, token)
    elif auth.invited_without_account(email):
        # Invited, never signed up. There is nothing to reset, and silence
        # would leave them waiting for an email that cannot come.
        mailer.send_reset_for_invitee(email)
    return answer


@app.get("/api/auth/reset")
def api_reset_check(token: str = "") -> dict:
    """Whether a reset link is still good, without spending it.

    The page calls this on load so it can show the form or an expired notice.
    Deliberately not a consuming operation — see `auth.reset_token_valid`.
    """
    return {"valid": auth.reset_token_valid(token)}


@app.post("/api/auth/reset")
def api_reset(body: ResetIn, request: Request, response: Response) -> dict:
    """Finish a reset: spend the token, set the password, sign them in.

    Signing them in here is deliberate. They have just proved control of the
    mailbox and chosen a password; bouncing them to a login form to type it
    again is friction with no security value. Every *other* session is gone,
    because `set_password` drops them all.
    """
    try:
        user = auth.complete_password_reset(body.token, body.password)
    except auth.AuthError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    auth.clear_attempts("login:email", user["email"])
    token = auth.start_session(user["id"], request.headers.get("user-agent", ""))
    _set_cookie(response, token)
    return _me(user)


@app.post("/api/auth/logout")
def api_logout(request: Request, response: Response) -> dict:
    auth.end_session(request.cookies.get(auth.COOKIE_NAME))
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


# --------------------------------------------------------------------------
# Sign-in pages
#
# Real addresses, because a reset link has to land somewhere and a password
# manager needs a page it can recognise. See a2b/authpages.py.
# --------------------------------------------------------------------------

def _already_signed_in(request: Request) -> bool:
    return auth.user_for_token(request.cookies.get(auth.COOKIE_NAME)) is not None


@app.get("/login", response_class=HTMLResponse)
def page_login(request: Request, next: str = "") -> Any:
    # Someone who is already signed in asking for the sign-in page almost always
    # wants the thing behind it — a stale bookmark, or the browser restoring
    # tabs. Sending them on is kinder than showing a form they do not need.
    if _already_signed_in(request):
        return RedirectResponse(authpages.safe_next(next), status_code=303)
    return HTMLResponse(authpages.login_page(next))


@app.get("/signup", response_class=HTMLResponse)
def page_signup(request: Request, next: str = "") -> Any:
    if _already_signed_in(request):
        return RedirectResponse(authpages.safe_next(next), status_code=303)
    return HTMLResponse(authpages.signup_page(next))


@app.get("/forgot-password", response_class=HTMLResponse)
def page_forgot() -> str:
    return authpages.forgot_page()


@app.get("/reset-password", response_class=HTMLResponse)
def page_reset(token: str = "") -> str:
    """Render the reset form, or an expired notice.

    Validity is checked here so the person is told the truth on arrival rather
    than after choosing a password. Checking does not consume the token — a
    mail client that prefetches the link must not spend it.
    """
    return authpages.reset_page(token, auth.reset_token_valid(token))


@app.get("/logout")
def page_logout(request: Request) -> Any:
    """Sign out from a plain link.

    The API route is a POST, which is right for it, but a link in a nav bar
    cannot POST without script. This exists so signing out never depends on
    JavaScript having loaded — the one action a person most needs to work on a
    shared machine.

    A state-changing GET normally invites cross-site abuse, and the cookie's
    `SameSite=Lax` is what makes it safe here: Lax withholds the cookie from
    subresource requests, so the `<img src=".../logout">` that would otherwise
    sign people out from any page on the internet sends no session and does
    nothing. A deliberate top-level click still works, and its worst outcome is
    being signed out.
    """
    response = RedirectResponse("/login", status_code=303)
    auth.end_session(request.cookies.get(auth.COOKIE_NAME))
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return response


@app.get("/api/auth/me")
def api_me(request: Request) -> dict:
    """Never 401s. The console calls this on boot to decide which screen to
    show, and an anonymous visitor is an expected answer, not an error."""
    user = guard.current_user(request)
    return _me(user) if user else {"user": None, "vendors": []}


def _profile(user_id: str) -> dict:
    """Everything the account page shows, in one call.

    Fuller than `/auth/me`, which is deliberately thin because the console calls
    it on every boot. This one reads the row rather than the session, so it can
    carry `created_at` and `last_login_at` — neither of which is in the session
    token — alongside the workspaces this person can reach and how many browsers
    are currently signed in as them.
    """
    user = auth.get_user(user_id)
    if not user:
        raise HTTPException(404, "No such account.")
    return {
        "user": user,
        "vendors": auth.vendors_for(user["email"]),
        "staff": auth.is_staff(user["email"]),
        "sessions": auth.session_count(user_id),
        # The address is identity here, not a preference. See auth.update_profile.
        "email_editable": False,
    }


@app.get("/api/auth/profile")
def api_profile(request: Request) -> dict:
    user = guard.require_user(request)
    return _profile(user["id"])


@app.post("/api/auth/profile")
def api_update_profile(body: ProfileIn, request: Request) -> dict:
    user = guard.require_user(request)
    try:
        auth.update_profile(user["id"], body.name)
    except auth.AuthError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    return _profile(user["id"])


@app.post("/api/auth/sessions/revoke-others")
def api_revoke_other_sessions(request: Request) -> dict:
    """Sign out every other browser, keeping the one that asked."""
    user = guard.require_user(request)
    removed = auth.end_other_sessions(user["id"], request.cookies.get(auth.COOKIE_NAME))
    return {"ok": True, "ended": removed, "sessions": auth.session_count(user["id"])}


@app.post("/api/auth/password")
def api_change_password(body: PasswordIn, request: Request, response: Response) -> dict:
    user = guard.require_user(request)
    try:
        auth.authenticate(user["email"], body.current_password)
        auth.set_password(user["id"], body.new_password)
    except auth.AuthError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    # set_password dropped every session including this one; issue a new one so
    # the user is not signed out of the tab they just used.
    _set_cookie(response, auth.start_session(user["id"]))
    return {"ok": True}


# --------------------------------------------------------------------------
# Health and registry
# --------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "industries": len(registry.industries()),
        "products": len(registry.products()),
        "templates": len(registry.templates()),
        "apps": len(registry.apps()),
    }


@app.get("/api/registry/industries")
def api_industries() -> dict:
    return {
        "industries": [
            {"key": i.key, "name": i.name, "templates": i.templates, "blurb": i.blurb}
            for i in registry.industries().values()
        ]
    }


@app.get("/api/registry/products")
def api_products(industry: Optional[str] = None) -> dict:
    prods = registry.products_for_industry(industry) if industry else list(registry.products().values())
    return {
        "products": [
            {"key": p.key, "name": p.name, "industry": p.industry, "tier": p.tier,
             "template": p.template, "has_template": bool(p.template)}
            for p in prods
        ]
    }


@app.get("/api/registry/apps")
def api_apps() -> dict:
    return {
        "apps": [
            {"id": a.id, "icon": a.icon, "name": a.name, "tag": a.tag, "wave": a.wave,
             "blurb": a.blurb, "implemented": a.implemented}
            for a in registry.apps().values()
        ]
    }


@app.get("/api/registry/library")
def api_library() -> dict:
    return {
        "library": {
            key: [
                {"name": s.name, "summary": s.summary, "says": s.says,
                 "apps": s.apps, "asks": s.asks, "collects": s.collects}
                for s in secs
            ]
            for key, secs in registry.library().items()
        }
    }


def _public_section(s) -> dict:
    return {
        "name": s.name, "summary": s.summary, "says": s.says,
        "asks": s.asks, "collects": s.collects, "apps": s.apps,
    }


@app.get("/api/registry/core")
def api_core(industry: Optional[str] = None) -> dict:
    """Each industry's core intake, the sections every skill in it seeds from."""
    core = registry.core_sections()
    if industry:
        core = {k: v for k, v in core.items() if k == industry}
    return {"core": {k: [_public_section(s) for s in v] for k, v in core.items()}}


@app.get("/api/registry/templates")
def api_templates(industry: Optional[str] = None) -> dict:
    """The authored (and generic) intakes, readable without an account.

    The homepage library is a showroom: anyone should be able to read the
    questions themselves before they paste a URL or create a workspace.
    """
    items = []
    for t in registry.templates().values():
        if industry and t.industry != industry:
            continue
        items.append({
            "id": t.id,
            "name": t.name,
            "industry": t.industry,
            "product_key": t.product_key,
            "version": t.version,
            "tailored": not str(t.version).startswith("generic-"),
            "sections": [_public_section(s) for s in t.sections],
        })
    items.sort(key=lambda x: (x["industry"], 0 if x["tailored"] else 1, x["name"]))
    return {"templates": items}


# --------------------------------------------------------------------------
# Vendors
# --------------------------------------------------------------------------

@app.get("/api/vendors")
def api_list_vendors(request: Request) -> dict:
    """Only the vendors the caller belongs to. There is no route that lists
    every tenant on the instance."""
    user = guard.require_user(request)
    return {"vendors": auth.vendors_for(user["email"])}


@app.post("/api/vendors")
def api_create_vendor(body: VendorIn, request: Request) -> dict:
    user = guard.require_user(request)
    if body.industry_key not in registry.industries():
        raise HTTPException(400, f"unknown industry {body.industry_key!r}")
    vid = skills.create_vendor(
        name=body.name, industry_key=body.industry_key,
        website=body.website, description=body.description,
    )
    # Without this the creator cannot read back what they just made.
    skills.add_member(vid, user["email"], role="admin", status="active")
    return {"vendor": skills.get_vendor(vid), "role": "admin"}


@app.get("/api/vendors/{vendor_id}")
def api_get_vendor(vendor_id: str, request: Request) -> dict:
    who = guard.vendor(request, vendor_id, "viewer")
    return {
        "vendor": skills.get_vendor(vendor_id),
        "skills": skills.list_skills(vendor_id),
        "members": skills.list_members(vendor_id),
        "funnel": sessions.funnel_summary(vendor_id),
        "role": who["role"],
    }


@app.post("/api/vendors/{vendor_id}/members")
def api_add_member(vendor_id: str, body: MemberIn, request: Request) -> dict:
    guard.vendor(request, vendor_id, "admin")
    if body.role not in auth.ROLES:
        raise HTTPException(400, f"role must be one of {', '.join(auth.ROLES)}")
    # 'invited' until they have an account; create_user flips it on signup.
    skills.add_member(vendor_id, body.email, body.role,
                      status="active" if auth.user_exists(body.email) else "invited")
    return {"members": skills.list_members(vendor_id)}


@app.delete("/api/vendors/{vendor_id}/members")
def api_remove_member(vendor_id: str, email: str = Query(...), *, request: Request) -> dict:
    who = guard.vendor(request, vendor_id, "admin")
    if email.lower() == who["user"]["email"]:
        raise HTTPException(409, "You cannot remove your own access.")
    admins = [m for m in skills.list_members(vendor_id) if m["role"] == "admin"]
    if len(admins) <= 1 and any(m["email"] == email.lower() for m in admins):
        raise HTTPException(409, "A vendor must keep at least one admin.")
    skills.remove_member(vendor_id, email)
    return {"members": skills.list_members(vendor_id)}


# --------------------------------------------------------------------------
# Ingest — suggest, then confirm
# --------------------------------------------------------------------------

@app.post("/api/ingest/scan")
def api_scan(body: ScanIn, request: Request) -> dict:
    # Deliberately open to anonymous callers: scanning your own site is the
    # first thing a prospective vendor does, and it happens before signup. It
    # writes an ingest_run with a null vendor_id, which is inert until someone
    # signed in confirms it. Binding a run to an existing vendor is not.
    if body.vendor_id:
        guard.vendor(request, body.vendor_id, "editor")
    brand = None
    try:
        if body.html:
            # A caller supplying the HTML has one page and no way to give us
            # more, so this stays the single-page reader it always was.
            scan = ingest_mod.scan_html(body.url, body.html)
            ranked = ingest_mod.detect_industry(scan)
            industry_key = ranked[0][0] if ranked else None
            matches = ingest_mod.match_scan(scan, industry_key)
        else:
            # Otherwise read the site the way the scanner does. A vendor
            # scanning their own site at signup used to get a shorter list than
            # we got from the outside, which is the wrong way round.
            preview = prospect.preview(body.url)
            scan = ingest_mod.Scan(url=preview.url, title=preview.name,
                                   headings=[], links=[], text="")
            industry_key = preview.industry_key
            ranked = preview.industry_scores
            matches = prospect.as_matches(preview)
            brand = preview.brand
    except PermissionError as exc:
        # Not a failure to reach them, and not ours. 502 would say their server
        # broke, when what happened is that they answered.
        raise HTTPException(403, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"could not fetch {body.url}: {exc}") from exc

    run_id = ingest_mod.save_run(body.vendor_id, scan, industry_key, matches)
    out = {
        "run_id": run_id,
        "url": scan.url,
        "title": scan.title,
        "industry_suggestions": [
            {"key": k, "name": registry.industries()[k].name, "score": round(s, 3)}
            for k, s in ranked[:4]
        ],
        # Nothing is bound yet. The vendor confirms every row.
        "matches": ingest_mod.get_run(run_id)["matches"],
    }
    # Only when the site showed one clearly. A supplied page has no host behind
    # it to read a logo from, so it never carries one.
    if brand:
        out["brand"] = brand
    return out


@app.post("/api/ingest/{run_id}/confirm")
def api_confirm(run_id: str, body: ConfirmIn, request: Request) -> dict:
    # This is the step that turns proposals into the vendor's own skills, so
    # this is the step that needs a role.
    guard.vendor(request, body.vendor_id, "editor")
    if body.industry_key:
        ingest_mod.confirm_industry(run_id, body.industry_key)
    created = ingest_mod.confirm_matches(run_id, body.vendor_id, body.match_ids)
    return {"created_skill_ids": created, "skills": skills.list_skills(body.vendor_id)}


# --------------------------------------------------------------------------
# Prospect scanner — read a real website, write nothing
# --------------------------------------------------------------------------

@app.get("/api/prospect/shortlist")
def api_prospect_shortlist(request: Request) -> dict:
    """Real businesses to point the scanner at.

    Bundled rather than typed each time so the demo is repeatable and so nobody
    has to remember a bank's URL in front of an audience. Signed-in only: these
    are public websites, but a list of named institutions sitting on our own
    open endpoint invites a reading of it that is not true.
    """
    guard.require_user(request)
    path = CONTENT_DIR / "prospect_banks.json"
    if not path.is_file():
        return {"banks": [], "as_of": ""}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"banks": data.get("banks", []), "as_of": data.get("as_of", "")}


@app.post("/api/prospect/preview")
def api_prospect_preview(body: ProspectIn, request: Request) -> dict:
    """Scan a live website and return the product page we would build from it.

    Writes nothing — no ingest run, no matches, no vendor. That is the whole
    difference from `/api/ingest/scan`, and it is what makes this safe to point
    at businesses who have not asked us to and have no relationship with us.
    Binding still happens through the ingest path, behind a role.

    Signed-in only. It spends our outbound requests on a host the caller names,
    which is not something to leave open.
    """
    guard.require_user(request)
    try:
        return prospect.preview(body.url,
                                max_pages=body.max_pages or prospect.MAX_PAGES,
                                name=body.name or "").as_dict()
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"could not read {body.url}: {exc}") from exc


@app.get("/api/ingest/demand")
def api_demand(request: Request) -> dict:
    """Unmatched offerings across every scan — the template roadmap.

    Aggregated across tenants, so it is Lenni's data rather than any one
    vendor's. This asked for a staff check as soon as there was a notion of
    staff; there is one now, and until today any bank's signed-in user could
    read a list of what every other bank on the platform offers.
    """
    guard.staff(request)
    return {"unmatched": ingest_mod.unmatched_demand()}


# --------------------------------------------------------------------------
# Internal portal
#
# The only routes in this file that read across tenants. Each one calls
# `guard.staff` as its first statement and answers 404 to everyone else, so a
# vendor who finds the URL learns nothing from the reply.
# --------------------------------------------------------------------------

@app.get("/api/admin/overview")
def api_admin_overview(request: Request, days: int = Query(30, ge=1, le=365)) -> dict:
    guard.staff(request)
    return analytics.platform(days=days)


@app.get("/api/admin/companies/{vendor_id}")
def api_admin_company(request: Request, vendor_id: str) -> dict:
    user = guard.staff(request)
    detail = analytics.company(vendor_id)
    if not detail:
        raise HTTPException(404, "Not found.")
    analytics.record_access(user["email"], "company", vendor_id=vendor_id)
    return detail


@app.get("/api/admin/runs/{session_id}")
def api_admin_run(request: Request, session_id: str) -> dict:
    """One customer's run, answers included.

    The single route in the system that hands a bank's customer's financial
    details to someone outside that bank. Logged before it returns.
    """
    user = guard.staff(request)
    run = sessions.get_session(session_id)
    if not run:
        raise HTTPException(404, "Not found.")
    analytics.record_access(user["email"], "run", vendor_id=run.get("vendor_id"),
                            session_id=session_id)
    skill = skills.get_skill(run["skill_id"]) if run.get("skill_id") else None
    profile = sessions.get_profile(run["profile_id"]) if run.get("profile_id") else None
    return {
        "run": run,
        "skill": {"id": skill["id"], "name": skill["name"],
                  "sections": skill["sections"]} if skill else None,
        "profile": profile,
        "vendor": skills.get_vendor(run["vendor_id"]) if run.get("vendor_id") else None,
    }


@app.get("/api/admin/access-log")
def api_admin_access_log(request: Request) -> dict:
    """Who among us opened what. Visible to staff, including to whoever is
    being looked at in it — an audit trail nobody can read is decoration."""
    guard.staff(request)
    return {"access": analytics.access_log()}


# --------------------------------------------------------------------------
# Skills
# --------------------------------------------------------------------------

@app.post("/api/vendors/{vendor_id}/skills/seed")
def api_seed(vendor_id: str, request: Request, template_id: str = Query(...)) -> dict:
    guard.vendor(request, vendor_id, "editor")
    try:
        sid = skills.seed_skill_from_template(vendor_id, template_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"skill": skills.get_skill(sid)}


@app.post("/api/vendors/{vendor_id}/skills/custom")
def api_custom(vendor_id: str, request: Request, name: str = Query(...)) -> dict:
    guard.vendor(request, vendor_id, "editor")
    return {"skill": skills.get_skill(skills.create_custom_skill(vendor_id, name))}


def _links_for(vendor: dict, skill: dict) -> dict:
    """Start links for a skill, with its draft state made explicit.

    A draft skill has no public start page, so every link it produces dead-ends.
    The console still needs to show them — previewing what will ship is the point
    of the panel — but it has to say so rather than handing over a button that
    looks live. One went out to a customer's Claude and 404'd.
    """
    out = start_links(
        vendor_website=vendor["website"],
        vendor_slug=vendor["slug"],
        skill_slug=skill["slug"],
        entry_prompt=skill["entry_prompt"] or "",
        fallback_prompt=compile_short_entry_prompt(
            vendor_name=vendor["name"],
            skill_name=skill["name"],
            section_names=[s["name"] for s in skill["sections"]],
            workflow_url=hosted_start_url(vendor["slug"], skill["slug"], fmt="md"),
            vendor_slug=vendor["slug"],
            skill_slug=skill["slug"],
        ),
    )
    out["live"] = skill["status"] == "published"
    # The copy-and-paste form, for a customer whose assistant cannot open links.
    # Offered, never sent: it is the whole intake in one message, which is what
    # the staged prompt exists to avoid.
    out["full_prompt"] = compile_full_entry_prompt(
        vendor_name=vendor["name"], skill_name=skill["name"],
        sections=skill["sections"],
        vendor_slug=vendor["slug"], skill_slug=skill["slug"],
    )
    return out


@app.get("/api/skills/{skill_id}")
def api_get_skill(skill_id: str, request: Request) -> dict:
    guard.skill(request, skill_id, "viewer")
    skill = skills.get_skill(skill_id)
    if not skill:
        raise HTTPException(404, "no such skill")
    vendor = skills.get_vendor(skill["vendor_id"])
    fp = compile_final_prompt(dest=skill["destination"], vendor_name=vendor["name"])
    ok, blockers = can_publish(skill["destination"])
    return {
        "skill": skill,
        "vendor": vendor,
        "links": _links_for(vendor, skill),
        "final_prompt": {
            "opening": fp.opening,
            "produced": fp.produced,
            "delivery": fp.delivery,
            "link_line": fp.link_line,
            "sign_off": fp.sign_off,
            "locked": list(fp.locked),
            "text": fp.text(),
        },
        # Both halves, on purpose. The console shows the vendor's wording where
        # they wrote some and ours everywhere else, and it can only send back
        # what actually differs — which is what keeps a paragraph nobody edited
        # tracking our copy instead of freezing the day they opened the editor.
        "prompt_texts": skill["prompt_texts"],
        "prompt_defaults": entry_prompt_defaults(len(skill["sections"])),
        "warnings": delivery_warnings(skill["destination"]),
        "can_publish": ok,
        "publish_blockers": blockers,
    }


@app.patch("/api/skills/{skill_id}")
def api_rename(skill_id: str, request: Request, name: str = Query(...)) -> dict:
    guard.skill(request, skill_id)
    skills.rename_skill(skill_id, name)
    return {"skill": skills.get_skill(skill_id)}


@app.delete("/api/skills/{skill_id}")
def api_delete_skill(skill_id: str, request: Request) -> dict:
    # Destroying a skill is not an editing action; it discards work the whole
    # team may depend on, and there is no undo.
    guard.skill(request, skill_id, "admin")
    skills.delete_skill(skill_id)
    return {"deleted": True}


@app.post("/api/skills/{skill_id}/publish")
def api_publish(skill_id: str, request: Request) -> dict:
    guard.skill(request, skill_id)
    try:
        skill = skills.publish_skill(skill_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"skill": skill, "manifest_regenerated": True}


@app.post("/api/skills/{skill_id}/unpublish")
def api_unpublish(skill_id: str, request: Request) -> dict:
    guard.skill(request, skill_id)
    return {"skill": skills.unpublish_skill(skill_id)}


@app.put("/api/skills/{skill_id}/destination")
def api_destination(skill_id: str, body: DestinationIn, request: Request) -> dict:
    guard.skill(request, skill_id)
    skill = skills.get_skill(skill_id)
    if not skill:
        raise HTTPException(404, "no such skill")
    merged = dict(skill["destination"])
    for k, v in body.model_dump(exclude_none=True).items():
        if k == "outputs":
            merged["outputs"].update(v)
        elif k == "texts":
            merged.setdefault("texts", {}).update(v)
        else:
            merged[k] = v
    skills.set_destination(skill_id, merged)
    return api_get_skill(skill_id, request)


@app.put("/api/skills/{skill_id}/prompt-texts")
def api_prompt_texts(skill_id: str, body: PromptTextsIn, request: Request) -> dict:
    guard.skill(request, skill_id)
    if not skills.get_skill(skill_id):
        raise HTTPException(404, "no such skill")
    # exclude_unset rather than exclude_none: a field sent as null is how the
    # console puts one paragraph back to our wording, and dropping nulls here
    # would make that button do nothing.
    skills.set_prompt_texts(skill_id, body.model_dump(exclude_unset=True))
    return api_get_skill(skill_id, request)


@app.post("/api/skills/{skill_id}/connect-convey")
def api_connect_convey(skill_id: str, request: Request, url: str = Query(...)) -> dict:
    guard.skill(request, skill_id)
    skills.connect_convey(skill_id, url)
    return api_get_skill(skill_id, request)


@app.post("/api/skills/{skill_id}/entry-mode")
def api_entry_mode(skill_id: str, request: Request, mode: str = Query(...)) -> dict:
    guard.skill(request, skill_id)
    try:
        skills.set_entry_mode(skill_id, mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return api_get_skill(skill_id, request)


@app.get("/api/skills/{skill_id}/template-diff")
def api_template_diff(skill_id: str, request: Request) -> dict:
    guard.skill(request, skill_id, "viewer")
    return skills.template_diff(skill_id)


@app.post("/api/skills/{skill_id}/adopt-template")
def api_adopt(skill_id: str, request: Request, keep_added: bool = Query(True)) -> dict:
    guard.skill(request, skill_id)
    try:
        return {"skill": skills.adopt_template_version(skill_id, keep_added_sections=keep_added)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------

@app.post("/api/skills/{skill_id}/sections/library")
def api_add_library(skill_id: str, body: LibraryAddIn, request: Request) -> dict:
    guard.skill(request, skill_id)
    try:
        skills.add_library_section(skill_id, body.industry_key, body.index)
    except (KeyError, IndexError) as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"skill": skills.get_skill(skill_id)}


@app.post("/api/skills/{skill_id}/sections/blank")
def api_add_blank(skill_id: str, request: Request, name: str = Query("New section")) -> dict:
    guard.skill(request, skill_id)
    skills.add_blank_section(skill_id, name)
    return {"skill": skills.get_skill(skill_id)}


@app.put("/api/sections/{section_id}")
def api_update_section(section_id: str, body: SectionIn, request: Request) -> dict:
    guard.section(request, section_id)
    try:
        skills.update_section(section_id, **body.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@app.delete("/api/sections/{section_id}")
def api_delete_section(section_id: str, request: Request) -> dict:
    guard.section(request, section_id)
    try:
        skills.delete_section(section_id)
    except ValueError as exc:
        # Core sections cannot be removed. 409, not 400 — the request is
        # well-formed, it conflicts with the model's rules.
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@app.post("/api/sections/{section_id}/move")
def api_move_section(section_id: str, request: Request, delta: int = Query(...)) -> dict:
    guard.section(request, section_id)
    skills.move_section(section_id, delta)
    return {"ok": True}


@app.post("/api/sections/{section_id}/apps")
def api_add_app(section_id: str, request: Request, app_id: str = Query(...)) -> dict:
    guard.section(request, section_id)
    try:
        skills.add_app(section_id, app_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@app.delete("/api/sections/{section_id}/apps")
def api_remove_app(section_id: str, request: Request, app_id: str = Query(...)) -> dict:
    guard.section(request, section_id)
    skills.remove_app(section_id, app_id)
    return {"ok": True}


# --------------------------------------------------------------------------
# Sessions (console visibility)
# --------------------------------------------------------------------------

@app.get("/api/vendors/{vendor_id}/sessions")
def api_sessions(vendor_id: str, request: Request) -> dict:
    # Customer runs contain the applicant's own answers. Viewer is the floor,
    # but the point is that it is not public.
    guard.vendor(request, vendor_id, "viewer")
    return {"sessions": sessions.list_sessions(vendor_id)}


# --------------------------------------------------------------------------
# Public doors — the two publish outputs
# --------------------------------------------------------------------------

def _vendor_or_404(vendor_slug: str) -> dict:
    v = skills.get_vendor_by_slug(vendor_slug)
    if not v:
        raise HTTPException(404, "no such vendor")
    return v


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    """Say yes, in the one place a fetcher looks before deciding.

    Anthropic's fetcher asked for this, got a 404, and did not go on to read the
    guide it had been pointed at. A missing file is ambiguous rather than
    permissive, and a careful crawler is entitled to read ambiguity as no. These
    pages exist to be fetched by assistants; not saying so was an omission we
    could not see until a fetch stopped here.
    """
    return "\n".join([
        "# The pages under /v/ are published for AI assistants to read.",
        "User-agent: *",
        "Allow: /v/",
        "Allow: /",
        "",
        "# The console is a vendor's own workspace, and the API is not content.",
        "Disallow: /console/",
        "Disallow: /api/",
        "",
    ])


@app.get("/v/{vendor_slug}/llms.txt", response_class=PlainTextResponse)
def public_llms_txt(vendor_slug: str) -> str:
    return manifest_mod.llms_txt(_vendor_or_404(vendor_slug)["id"])


@app.get("/v/{vendor_slug}/llms.md", response_class=PlainTextResponse)
def public_llms_md(vendor_slug: str) -> str:
    return manifest_mod.llms_md(_vendor_or_404(vendor_slug)["id"])


@app.get("/v/{vendor_slug}/agents")
def public_agents(vendor_slug: str) -> dict:
    return manifest_mod.agents_page_data(_vendor_or_404(vendor_slug)["id"])


@app.get("/v/{vendor_slug}/webmaster")
def public_webmaster(vendor_slug: str) -> dict:
    return manifest_mod.webmaster_packet(_vendor_or_404(vendor_slug)["id"])


# Per-product docs. A customer clicked a loan, not a bank, so this is the pair a
# handed-off assistant is pointed at. The literal segments in the paths above keep
# these from colliding with the start routes.
@app.get("/v/{vendor_slug}/{skill_slug}/llms.txt", response_class=PlainTextResponse)
def public_skill_llms_txt(vendor_slug: str, skill_slug: str) -> str:
    try:
        return manifest_mod.skill_llms_txt(_vendor_or_404(vendor_slug)["id"], skill_slug)
    except KeyError:
        raise HTTPException(404, "no such published service")


@app.get("/v/{vendor_slug}/{skill_slug}/context.md", response_class=PlainTextResponse)
def public_skill_context_md(vendor_slug: str, skill_slug: str) -> str:
    vendor = _vendor_or_404(vendor_slug)
    try:
        body = manifest_mod.skill_context_md(vendor["id"], skill_slug)
    except KeyError:
        raise HTTPException(404, "no such published service")
    # "How do you know it read our questions?" is a fair question from a bank, and
    # this is where it gets answered. The assistant used to be asked to announce
    # the fetch in the chat, which put our plumbing in front of the customer and
    # took the word of the one party that might be making it up.
    skill = skills.get_skill_by_slug(vendor_slug, skill_slug)
    sessions.record_event("guide_read", vendor_id=vendor["id"], skill_id=skill["id"])
    return body


def _published_skill_or_404(vendor_slug: str, skill_slug: str) -> dict:
    skill = skills.get_skill_by_slug(vendor_slug, skill_slug)
    if not skill:
        raise HTTPException(404, "no such skill")
    if skill["status"] != "published":
        raise HTTPException(404, "this skill is not published")
    return skill


@app.get("/v/{vendor_slug}/start/{skill_slug}/{position}.md",
         response_class=PlainTextResponse)
def public_start_section(vendor_slug: str, skill_slug: str, position: int) -> str:
    """One section of the intake, and the address of the one after it.

    This is the chain that keeps the customer's opening message short. The start
    link carries section 1; when it is done the AI opens section 2 here, which
    names section 3, and so on until a page says it is the last.

    Serving the sections rather than shipping them all in the prompt also stops
    the vendor's complete question set being readable by anyone who is handed a
    start link — for a bank's underwriting criteria that matters.

    Not a substitute for the connector. Nothing here checks that the AI actually
    asked section 1 before fetching section 2, and nothing records the answers.
    It gets the pacing right; MCP is what makes it enforceable.
    """
    vendor = _vendor_or_404(vendor_slug)
    skill = _published_skill_or_404(vendor_slug, skill_slug)
    total = len(skill["sections"])
    if not 1 <= position <= total:
        raise HTTPException(404, f"this intake has {total} sections")

    section = skill["sections"][position - 1]
    sessions.record_event("section_read", vendor_id=vendor["id"], skill_id=skill["id"],
                          detail={"position": position, "name": section["name"]})

    out = [
        f"# {vendor['name']}: {skill['name']}",
        "",
        WORK_FROM_THE_PAGE,
        "",
        render_section(section, position, total),
        "",
        "## Next",
        "",
    ]
    if position < total:
        out += [
            "When these are answered, read back what you captured and end with this "
            "line, word for word:",
            "",
            f"> {SECTION_CHECK}",
            "",
            f"When they say so, open "
            f"{section_url(vendor_slug, skill_slug, position + 1)} for section "
            f"{position + 1} and ask that one the same way. Not before, and do not "
            f"read ahead.",
        ]
    else:
        fp = compile_final_prompt(dest=skill["destination"], vendor_name=vendor["name"])
        out += [
            "This is the last section. Once it is answered, ask this and wait:",
            "",
            f"> {SUMMARY_OFFER}",
            "",
            "When they say yes, read the whole package back — every section, then "
            "whatever is still outstanding — and end that message on this line, word "
            "for word, with nothing after it:",
            "",
            f"> {PDF_OFFER}",
            "",
            "Once they confirm, make the PDF. Only then talk about delivery: the "
            f"package is {destination_label(skill['destination'], vendor['name'])}, "
            "and they send it themselves.",
            "",
            "Say this to close:",
            "",
            fp.text(),
        ]
    out += ["", "## Terms", "", GUARDRAIL]
    return "\n".join(out)


# Registered before the HTML page so the `.md` suffix wins the match.
@app.get("/v/{vendor_slug}/start/{skill_slug}.md", response_class=PlainTextResponse)
def public_start_markdown(vendor_slug: str, skill_slug: str) -> str:
    """The whole workflow on one page.

    Not what the start link points at — that is the per-section chain above. This
    is the backstop: an assistant that cannot follow links, a customer who wants
    to read the intake before starting, and the source for the copy-everything
    prompt on the start page.
    """
    vendor = _vendor_or_404(vendor_slug)
    skill = _published_skill_or_404(vendor_slug, skill_slug)
    sessions.record_event("workflow_read", vendor_id=vendor["id"], skill_id=skill["id"])

    dest = destination_label(skill["destination"], vendor["name"])
    return "\n".join([
        f"# {vendor['name']}: {skill['name']}",
        "",
        f"Intake prepared with {vendor['name']}. Ask these sections in order, one at "
        f"a time, using the wording below. Wait for answers before moving on.",
        "",
        "After each section, read back what you captured and end with this line, "
        "word for word:",
        "",
        f"> {SECTION_CHECK}",
        "",
        "Move to the next section when they say so — not because the answers looked "
        "complete to you.",
        "",
        f"## The intake ({len(skill['sections'])} sections)",
        "",
        WORK_FROM_THE_PAGE,
        "",
        render_workflow(skill["sections"]) or "_No sections configured yet._",
        "",
        "## When it is complete",
        "",
        f"Ask **{SUMMARY_OFFER}** and wait. When they say yes, read the whole package "
        "back — every section, then whatever is still outstanding — and end that "
        "message on this line, word for word, with nothing after it:",
        "",
        f"> {PDF_OFFER}",
        "",
        f"Once they confirm, make the PDF. The finished package is {dest}; you have "
        "no channel there, so the customer sends it themselves. Do not raise delivery "
        "before the file exists, and do not claim to have sent anything.",
        "",
        "## Terms",
        "",
        GUARDRAIL,
        "",
        f"Published by {vendor['name']} via Lenni A2B. Regenerates on every change.",
    ])


@app.get("/v/{vendor_slug}/start/{skill_slug}", response_class=HTMLResponse)
def public_start(vendor_slug: str, skill_slug: str) -> str:
    """The universal start link: a terminal chooser that remembers the pick.

    This page exists because the per-assistant deep links in the demo do not
    universally work. Rather than five buttons of which some silently fail, this
    offers the prompt itself plus real links where they exist.
    """
    vendor = _vendor_or_404(vendor_slug)
    skill = _published_skill_or_404(vendor_slug, skill_slug)
    links = _links_for(vendor, skill)
    sessions.record_event("start_link_viewed", vendor_id=vendor["id"], skill_id=skill["id"])

    buttons = []
    for t in links["terminals"]:
        if t["supported"] and t["url"]:
            # Said on the button rather than in the small print: a customer who
            # expects the chat to start and lands on a filled-in box thinks it
            # broke.
            hint = "" if t["autosubmits"] else '<em>press enter to send</em>'
            buttons.append(
                f'<a class="term" href="{t["url"]}" target="_blank" rel="noopener" '
                f'data-terminal="{t["key"]}">Start in {t["name"]}{hint}</a>'
            )
        else:
            buttons.append(
                f'<button class="term copy" data-terminal="{t["key"]}">'
                f'Copy prompt for {t["name"]}</button>'
            )
    prompt = (skill["entry_prompt"] or "").replace("<", "&lt;")
    full = (links["full_prompt"] or "").replace("<", "&lt;")

    # Read by public/webmcp.js. Same-origin, so the base stays empty and the
    # page works unchanged behind a tunnel or a renamed host.
    page_cfg = json.dumps({"vendor": vendor_slug, "skill": skill_slug, "base": ""})

    # The paste-the-whole-intake escape hatch is for an assistant that cannot fetch
    # sections as it goes. On a connector-only skill it is a contradiction: the
    # vendor has said their questions live on their server, and this would publish
    # all of them on the page anyway. So it is replaced by the thing that actually
    # unblocks the customer, which is adding the connector.
    # Only the connector mode contradicts the paste-the-intake panel. In doc and
    # staged modes an assistant that cannot browse is exactly who it is for.
    if skill["entry_mode"] == "connector":
        fallback = (
            '<details><summary class="note">My assistant says it needs a connector</summary>'
            '<p class="note">This intake is served by '
            f'{vendor["name"]}, one section at a time, so your assistant needs the '
            'Lenni connector to run it. In Claude: <b>Settings &rarr; Connectors &rarr; '
            'Add custom connector</b>, then enter</p>'
            f'<pre>{MCP_BASE}</pre>'
            '<p class="note">On a work account an administrator may need to add it '
            'for your organisation first. Until it is there, your assistant will say '
            'so rather than guess at the questions.</p></details>'
        )
    else:
        fallback = (
            '<details><summary class="note">My assistant can\'t open links</summary>'
            '<p class="note">Some assistants have web access switched off, so they '
            'can\'t fetch each section as you go. Paste this instead — it contains '
            'the whole intake.</p>'
            f'<pre id="pf">{full}</pre>'
            '<button class="term copy" id="copyfull">Copy the full intake</button>'
            '</details>'
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Start {skill['name']} — {vendor['name']}</title>
<style>
 body{{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
   background:#f6f8fa;color:#16233a;margin:0;padding:40px 20px;}}
 .wrap{{max-width:620px;margin:0 auto;background:#fff;border:1px solid #e3e8ee;
   border-radius:14px;padding:28px 30px;}}
 h1{{font-size:20px;margin:0 0 4px;}} .sub{{color:#5a6b80;margin:0 0 22px;font-size:13.5px;}}
 .term{{display:block;width:100%;box-sizing:border-box;text-align:center;
   background:#16233a;color:#fff;border:0;border-radius:9px;padding:12px;margin-bottom:9px;
   font-size:14px;font-weight:600;cursor:pointer;text-decoration:none;}}
 .term.copy{{background:#fff;color:#16233a;border:1.5px solid #d4dbe4;}}
 .term em{{display:block;font-weight:400;font-size:11.5px;opacity:.7;font-style:normal;margin-top:2px;}}
 pre{{background:#0f1a2e;color:#d5e5df;border-radius:10px;padding:14px;font-size:11.5px;
   white-space:pre-wrap;overflow-x:auto;}}
 .note{{font-size:12px;color:#5a6b80;margin-top:16px;}}
</style>
<link rel="stylesheet" href="/static/webmcp.css">
</head><body><div class="wrap">
<h1>{skill['name']}</h1>
<p class="sub">{vendor['name']} &middot; guided by your own AI, section by section.</p>
{''.join(buttons)}
<p class="note">Your assistant will walk you through
{len(skill['sections'])} sections, one at a time. Preparation only — {vendor['name']}
makes all decisions.</p>
<details><summary class="note">See the prompt</summary><pre id="p">{prompt}</pre></details>
{fallback}
<div id="a2b-webmcp"></div>
</div>
<script>window.A2B_PAGE = {page_cfg};</script>
<script src="/static/webmcp.js"></script>
<script>
 var prompt = document.getElementById('p').textContent;
 // Absent on a connector-only skill, where there is no whole intake to paste.
 var copyfull = document.getElementById('copyfull');
 if (copyfull) {{
   copyfull.addEventListener('click', function(){{
     navigator.clipboard.writeText(document.getElementById('pf').textContent);
     this.textContent = 'Copied — paste it into a new chat';
   }});
 }}
 // Keyed on the attribute, not the class: the copy-the-full-intake button shares
 // the styling but must not be wired to the staged prompt.
 document.querySelectorAll('[data-terminal]').forEach(function(b){{
   b.addEventListener('click', function(){{
     localStorage.setItem('a2b_terminal', b.dataset.terminal);
     if (b.classList.contains('copy')) {{
       navigator.clipboard.writeText(prompt);
       b.textContent = 'Prompt copied — paste it into a new chat';
     }}
   }});
 }});
 var last = localStorage.getItem('a2b_terminal');
 if (last) {{
   var el = document.querySelector('[data-terminal="' + last + '"]');
   if (el) el.parentNode.insertBefore(el, el.parentNode.firstChild);
 }}
</script></body></html>"""


# --------------------------------------------------------------------------
# Live start links
# --------------------------------------------------------------------------

_NO_STORE = {"Cache-Control": "no-store"}


def _live_row(vendor_slug: str, product_slug: str) -> Optional[dict]:
    if not (live.valid_slug(vendor_slug) and live.valid_slug(product_slug)):
        return None
    return live.get(vendor_slug, product_slug)


def _live_or_404(vendor_slug: str, product_slug: str) -> dict:
    row = _live_row(vendor_slug, product_slug)
    if not row:
        raise HTTPException(404, "no such service")
    return row


@app.put("/api/live/{vendor_slug}/{product_slug}")
def api_live_put(vendor_slug: str, product_slug: str, body: LivePromptIn,
                 request: Request) -> dict:
    """The workspace reporting what it just published. Returns the fixed links."""
    who = guard.live_editor(request, vendor_slug)
    try:
        row = live.put(
            vendor_slug, product_slug,
            vendor_name=body.vendor_name, product_name=body.product_name,
            status=body.status, prompt=body.prompt, notice=body.notice, page=body.page,
            actor_email=who["user"]["email"],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"changed": row["changed"], "live": live.public_view(row)}


@app.get("/api/live/{vendor_slug}/{product_slug}")
def api_live_get(vendor_slug: str, product_slug: str) -> JSONResponse:
    """The current prompt, for a start page opened on a device that never saw
    the edit."""
    row = _live_or_404(vendor_slug, product_slug)
    return JSONResponse(live.public_view(row), headers=_NO_STORE)


@app.get("/api/live/{vendor_slug}")
def api_live_vendor(vendor_slug: str) -> JSONResponse:
    """Which of an organisation's products have fixed links, and what they are.

    One request when a workspace opens, so the embed code it copies can use the
    fixed links for exactly the products that have them. Carries no prompts.
    """
    rows = live.for_vendor(vendor_slug) if live.valid_slug(vendor_slug) else []
    return JSONResponse({
        "vendor": vendor_slug,
        "products": [{
            "slug": r["product_slug"], "status": r["status"], "version": r["version"],
            "updated_at": r["updated_at"], "links": live.links(vendor_slug, r["product_slug"]),
        } for r in rows],
    }, headers=_NO_STORE)


@app.get("/api/live/{vendor_slug}/{product_slug}/history")
def api_live_history(vendor_slug: str, product_slug: str, request: Request) -> dict:
    """Every change, newest first, for the people who may make one."""
    guard.live_editor(request, vendor_slug)
    _live_or_404(vendor_slug, product_slug)
    return {"history": live.history(vendor_slug, product_slug)}


_GO_CSS = (
    'body{font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;'
    "background:#f6f8fa;color:#16233a;margin:0;padding:40px 20px;}"
    ".wrap{max-width:620px;margin:0 auto;background:#fff;border:1px solid #e3e8ee;"
    "border-radius:14px;padding:28px 30px;}"
    "h1{font-size:20px;margin:0 0 4px;}.sub{color:#5a6b80;margin:0 0 22px;font-size:13.5px;}"
    ".term{display:block;width:100%;box-sizing:border-box;text-align:center;"
    "background:#16233a;color:#fff;border:0;border-radius:9px;padding:12px;margin-bottom:9px;"
    "font-size:14px;font-weight:600;cursor:pointer;text-decoration:none;}"
    ".term.copy{background:#fff;color:#16233a;border:1.5px solid #d4dbe4;}"
    ".term em{display:block;font-weight:400;font-size:11.5px;opacity:.7;font-style:normal;"
    "margin-top:2px;}"
    "pre{background:#0f1a2e;color:#d5e5df;border-radius:10px;padding:14px;font-size:11.5px;"
    "white-space:pre-wrap;overflow-x:auto;}"
    ".note{font-size:12px;color:#5a6b80;margin-top:16px;}"
)


def _go_html(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_esc(title)}</title><style>{_GO_CSS}</style></head>"
        f'<body><div class="wrap">{body}</div></body></html>',
        status_code=status, headers=_NO_STORE,
    )


def _go_missing() -> HTMLResponse:
    # A customer followed a button on a bank's page. A JSON 404 would read as the
    # bank's site being broken; this at least says where to look.
    return _go_html(
        "Link not active",
        "<h1>This link isn't active</h1><p class=\"sub\">The service may have moved or "
        "been taken down. Check the bank's website for the current link.</p>",
        status=404,
    )


def _live_sections(page: Any) -> list[str]:
    product = page.get("product") if isinstance(page, dict) else None
    names = product.get("sections") if isinstance(product, dict) else None
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


# Registered before the page so the `.md` suffix wins the match.
@app.get("/go/{vendor_slug}/{product_slug}.md", response_class=PlainTextResponse)
def go_workflow(vendor_slug: str, product_slug: str) -> str:
    """The prompt as text, for an assistant handed the short form of a link.

    A prompt too long for a URL goes out as "open this address and follow it",
    and this is the address.
    """
    row = _live_or_404(vendor_slug, product_slug)
    if row["status"] != "published":
        raise HTTPException(404, "this service is not published")
    return "\n".join([
        f"# {row['vendor_name']}: {row['product_name']}",
        "",
        row["prompt"],
        "",
        f"Published by {row['vendor_name']} via Lenni A2B. Regenerates on every change.",
    ])


@app.get("/go/{vendor_slug}/{product_slug}/{terminal}")
def go_terminal(vendor_slug: str, product_slug: str, terminal: str) -> Response:
    """A start button on a bank's page.

    Resolved at the click, not when the button was pasted, so the prompt in the
    redirect is whatever the workspace last published.
    """
    row = _live_row(vendor_slug, product_slug)
    if not row or terminal not in live.TERMINAL_KEYS:
        return _go_missing()
    url = live.destination(row, terminal)
    if not url:
        # Unpublished, or Copilot, which takes no prompt from a link. The page
        # says which, and offers the prompt to copy where there is one.
        return RedirectResponse(f"/go/{vendor_slug}/{product_slug}", status_code=302,
                                headers=_NO_STORE)
    sessions.record_event("live_link_opened", vendor_id=auth.vendor_for_slug(vendor_slug), detail={
        "vendor": vendor_slug, "product": product_slug, "terminal": terminal,
        "version": row["version"],
    })
    return RedirectResponse(url, status_code=302, headers=_NO_STORE)


@app.get("/go/{vendor_slug}/{product_slug}", response_class=HTMLResponse)
def go_page(vendor_slug: str, product_slug: str) -> HTMLResponse:
    """The single address to share when a bank wants one link rather than three,
    and where a button lands when it cannot open an assistant itself."""
    row = _live_row(vendor_slug, product_slug)
    if not row:
        return _go_missing()
    vendor, product = _esc(row["vendor_name"]), _esc(row["product_name"])
    title = f"{row['product_name']} — {row['vendor_name']}"
    if row["status"] != "published":
        notice = row["notice"] or f"{row['product_name']} isn't taking applications right now."
        return _go_html(title, f'<h1>{product}</h1><p class="sub">{vendor}</p>'
                               f"<p>{_esc(notice)}</p>")

    buttons = []
    for t in TERMINALS:
        if t.supported and t.template:
            # Said on the button, as on the hosted start page: a customer who
            # expects the chat to start and lands on a filled-in box thinks it broke.
            hint = "" if t.autosubmits else "<em>press enter to send</em>"
            buttons.append(
                f'<a class="term" href="/go/{vendor_slug}/{product_slug}/{t.key}" '
                f'target="_blank" rel="noopener">Start in {_esc(t.name)}{hint}</a>'
            )
        else:
            buttons.append(f'<button class="term copy" type="button" data-copy>'
                           f"Copy prompt for {_esc(t.name)}</button>")
    sections = _live_sections(row["page"])
    walk = (f"Your assistant will walk you through {len(sections)} sections, one at a "
            "time. " if sections else "")
    return _go_html(f"Start {title}", (
        f'<h1>{product}</h1><p class="sub">{vendor} &middot; guided by your own AI.</p>'
        + "".join(buttons)
        + f'<p class="note">{walk}Preparation only — {vendor} makes all decisions.</p>'
        f'<details><summary class="note">See the prompt</summary>'
        f'<pre id="p">{_esc(row["prompt"])}</pre></details>'
        "<script>document.querySelectorAll('[data-copy]').forEach(function(b){"
        "b.addEventListener('click',function(){"
        "navigator.clipboard.writeText(document.getElementById('p').textContent);"
        "b.textContent='Prompt copied — paste it into a new chat';});});</script>"
    ))


# --------------------------------------------------------------------------
# MCP endpoint
# --------------------------------------------------------------------------

@app.get("/mcp")
def mcp_root(request: Request, vendor: Optional[str] = None,
             skill: Optional[str] = None) -> Response:
    """Two audiences on one path.

    An MCP client issues `GET` here to open a server-to-client event stream. We
    have nothing to push — every tool answers inside its own response — so that
    case gets the 405 the specification provides for exactly this, which clients
    treat as "POST only" and carry on.

    Anything else arriving by `GET` is a person or a browsing assistant looking
    the endpoint up, and the honest answer is what this is and how to attach to
    it. It used to be a bare 404, and assistants sent here by a launch link
    reasonably concluded the vendor was offline.
    """
    if "text/event-stream" in (request.headers.get("accept") or "").lower():
        return Response(status_code=405, headers={"Allow": "POST, DELETE"})

    body: dict[str, Any] = {
        "service": "Lenni A2B",
        "description": (
            "MCP endpoint, Streamable HTTP transport. Add this URL as a custom "
            "connector in Claude or ChatGPT; it cannot be attached from a URL "
            "named inside a prompt."
        ),
        "transport": "streamable-http",
        "protocol_versions": list(transport.PROTOCOL_VERSIONS),
        "connector_url": MCP_BASE,
        "authentication": "bearer" if transport.required_token() else "none",
        # The console's own preview shape. Kept because the in-page bridge and
        # the console both read it, and it is not what a connector speaks.
        "console_tools_url": f"{MCP_BASE}/tools",
        "console_call_url": f"{MCP_BASE}/call",
    }
    if vendor:
        body["vendor_manifest"] = f"{PUBLIC_BASE}/v/{vendor}/llms.md"
        if skill:
            # The question the caller was really asking.
            body["workflow"] = hosted_start_url(vendor, skill, fmt="md")
            body["start_page"] = hosted_start_url(vendor, skill)
    return JSONResponse(body)


@app.post("/mcp")
async def mcp_transport(request: Request) -> Response:
    """The connector itself: one JSON-RPC message in, one response out."""
    if not transport.authorized(request.headers.get("authorization")):
        return JSONResponse(
            {"error": "unauthorized"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="lenni-a2b"'},
        )

    try:
        message = await request.json()
    except Exception:
        return JSONResponse(transport.parse_error(), status_code=400)

    reply = transport.handle(message, session_id=request.headers.get("mcp-session-id"))

    headers = {"Mcp-Session-Id": reply.session_id} if reply.session_id else {}
    if reply.body is None:
        return Response(status_code=reply.status, headers=headers)
    return JSONResponse(reply.body, status_code=reply.status, headers=headers)


@app.delete("/mcp")
def mcp_close(request: Request) -> Response:
    """Client hanging up. Forgetting the connection is all there is to do."""
    transport.close_connection(request.headers.get("mcp-session-id"))
    return Response(status_code=204)


@app.get("/mcp/tools")
def mcp_tools() -> dict:
    return {"tools": list_tools()}


@app.post("/mcp/call")
def mcp_call(body: ToolCallIn) -> dict:
    ctx = ToolContext(vendor_slug=body.vendor, email=body.email, session_id=body.session_id)
    try:
        return {"result": call_tool(body.name, body.arguments, ctx)}
    except ToolError as exc:
        return exc.to_dict()


# --------------------------------------------------------------------------
# Console
# --------------------------------------------------------------------------

if CONSOLE_DIR.exists():
    app.mount("/console", StaticFiles(directory=str(CONSOLE_DIR), html=True), name="console")

# The internal portal's files are served to anyone who asks, and they are worth
# nothing without a session on the staff list: the page is a shell that renders
# whatever `/api/admin/*` returns, and those answer 404 to everybody else.
# Hiding the HTML would be security by URL, and it would also mean a staff
# member who is signed out gets a blank screen instead of a sign-in box.
if ADMIN_DIR.exists():
    app.mount("/internal", StaticFiles(directory=str(ADMIN_DIR), html=True), name="internal")

if PUBLIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return (
        '<html><body style="font:15px system-ui;padding:40px;max-width:640px;margin:auto">'
        f"<h1>Lenni A2B {__version__}</h1>"
        "<p>Agent-to-Business. Vendors publish services as skills their customers' AI can run.</p>"
        '<ul><li><a href="/console/">Vendor console</a></li>'
        '<li><a href="/docs">API documentation</a></li>'
        '<li><a href="/mcp/tools">MCP tool surface</a></li>'
        '<li><a href="/health">Health</a></li></ul></body></html>'
    )
