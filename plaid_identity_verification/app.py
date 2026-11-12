"""ProofLink: multi-tenant employee verification-link demo."""

from __future__ import annotations

import html
import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import db
from config import settings
from emailer import send_result_notification
from plaid_client import (
    PlaidError,
    create_identity_verification,
    get_identity_verification,
    normalize_result,
    verify_webhook,
)
from security import password_hash, password_verify, valid_password


LOGGER = logging.getLogger("lenni-verify")
COOKIE_NAME = "piv_session"
CONSENT_VERSION = "demo-2026-10-05"
TERMINAL_STATUSES = {
    "success",
    "failed",
    "pending_review",
    "canceled",
    "expired",
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="ProofLink Demo",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; form-action 'self' https://verify-sandbox.plaid.com https://verify.plaid.com; frame-ancestors 'none'; "
        "base-uri 'none'"
    )
    return response


def u(path: str = "") -> str:
    if not path:
        return settings.base_path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{settings.base_path}{path}"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("cf-connecting-ip") or request.headers.get(
        "x-forwarded-for"
    )
    if forwarded:
        return forwarded.split(",", 1)[0].strip()[:64]
    return request.client.host[:64] if request.client else "unknown"


async def form_data(request: Request) -> dict[str, str]:
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise HTTPException(413, "Form is too large.")
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type:
        raise HTTPException(415, "Expected a standard form submission.")
    parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def current_member(request: Request) -> dict | None:
    return db.member_for_session(request.cookies.get(COOKIE_NAME))


def set_session_cookie(response: HTMLResponse, raw_token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        raw_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path=settings.base_path or "/",
    )


def clear_session_cookie(response: HTMLResponse | RedirectResponse) -> None:
    response.delete_cookie(
        COOKIE_NAME,
        path=settings.base_path or "/",
    )


def csrf_valid(member: dict, form: dict[str, str]) -> bool:
    import hmac

    supplied = form.get("csrf", "")
    expected = member.get("csrf_token", "")
    return bool(supplied and expected and hmac.compare_digest(supplied, expected))


def status_label(status: str) -> str:
    return {
        "active": "In progress",
        "success": "Passed",
        "failed": "Could not verify",
        "pending_review": "Needs review",
        "canceled": "Canceled",
        "expired": "Expired",
        "error": "System error",
    }.get(status, status.replace("_", " ").title())


def status_class(status: str) -> str:
    if status == "success":
        return "good"
    if status in {"failed", "error", "canceled", "expired"}:
        return "bad"
    if status == "pending_review":
        return "warn"
    return "neutral"


def shell(
    title: str,
    body: str,
    *,
    member: dict | None = None,
    public_flow: bool = False,
) -> str:
    if public_flow:
        nav = '<span class="nav-context">Secure customer verification</span>'
    elif member:
        nav = (
            f'<a href="{u("/dashboard")}">Dashboard</a>'
            f'<form class="nav-form" method="post" action="{u("/logout")}">'
            f'<input type="hidden" name="csrf" value="{esc(member["csrf_token"])}">'
            '<button class="link-button" type="submit">Sign out</button></form>'
        )
    else:
        nav = (
            f'<a href="{u("/login")}">Employee sign in</a>'
            f'<a class="nav-cta" href="{u("/")}#create">Create organization</a>'
        )
    mode = "Plaid Sandbox" if settings.provider == "plaid" else "Safe demo mode"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{esc(title)} · ProofLink</title>
  <link rel="stylesheet" href="{u("/static/app.css")}">
  <script defer src="{u("/static/app.js")}"></script>
</head>
<body>
  <header class="topbar">
    <a class="brand" href="{u("/")}">
      <span class="brand-mark" aria-hidden="true">L</span>
      <span>Proof<strong>Link</strong></span>
    </a>
    <nav>{nav}</nav>
  </header>
  <main>{body}</main>
  <footer>
    <span>Identity verification, not a credit decision.</span>
    <span class="mode-dot"><i></i>{esc(mode)}</span>
  </footer>
</body>
</html>"""


def error_box(message: str) -> str:
    return f'<div class="alert error" role="alert">{esc(message)}</div>'


def landing(error: str | None = None) -> str:
    alert = error_box(error) if error else ""
    body = f"""
<section class="hero">
  <div class="eyebrow">Digital KYC for professionals</div>
  <h1>One trusted link for every employee.</h1>
  <p class="hero-copy">
    An organization opens enrollment. Every employee who joins receives a
    permanent personal link for starting customer identity checks.
  </p>
  <div class="flow-row">
    <div><b>1</b><span>Employee joins</span></div>
    <div><b>2</b><span>Unique link appears</span></div>
    <div><b>3</b><span>Customer verifies</span></div>
    <div><b>4</b><span>Employee is notified</span></div>
  </div>
</section>
<section class="split-section" id="create">
  <div>
    <div class="eyebrow dark">Organization setup</div>
    <h2>Create a verification workspace</h2>
    <p>
      The first account becomes the organization administrator. You will get an
      enrollment link to send to employees.
    </p>
    <ul class="check-list">
      <li>Employee enrollment can be closed or reopened</li>
      <li>Every employee gets a different permanent link</li>
      <li>Results remain scoped to the correct organization</li>
      <li>Emails contain outcomes, never sensitive identity data</li>
    </ul>
  </div>
  <div class="card form-card">
    <h3>Start the demo</h3>
    {alert}
    <form method="post" action="{u("/organizations")}">
      <label>Organization name
        <input name="organization_name" autocomplete="organization" maxlength="100" required
               placeholder="Bluebonnet Community Bank">
      </label>
      <label>Your name
        <input name="name" autocomplete="name" maxlength="100" required placeholder="Taylor Reed">
      </label>
      <label>Work email
        <input type="email" name="email" autocomplete="email" maxlength="200" required
               placeholder="taylor@company.com">
      </label>
      <label>Password
        <input type="password" name="password" autocomplete="new-password" required
               minlength="10" placeholder="10+ characters, with a number">
      </label>
      <button class="primary wide" type="submit">Create organization</button>
    </form>
    <p class="fine-print">
      Demo only. Do not enter real customer SSNs, ID numbers, or document images.
    </p>
  </div>
</section>
"""
    return shell("Employee verification links", body)


def login_page(
    error: str | None = None,
    *,
    next_path: str = "",
) -> str:
    alert = error_box(error) if error else ""
    next_field = (
        f'<input type="hidden" name="next" value="{esc(next_path)}">'
        if next_path
        else ""
    )
    body = f"""
<section class="narrow-page">
  <div class="card form-card">
    <div class="eyebrow dark">Employee portal</div>
    <h1>Sign in</h1>
    <p>Manage your personal verification link and see its verification activity.</p>
    {alert}
    <form method="post" action="{u("/login")}">
      {next_field}
      <label>Work email
        <input type="email" name="email" autocomplete="email" required>
      </label>
      <label>Password
        <input type="password" name="password" autocomplete="current-password" required>
      </label>
      <button class="primary wide" type="submit">Sign in</button>
    </form>
  </div>
</section>"""
    return shell("Sign in", body)


def join_page(invite: str, error: str | None = None) -> str:
    alert = error_box(error) if error else ""
    body = f"""
<section class="narrow-page">
  <div class="card form-card">
    <div class="eyebrow dark">Employee enrollment</div>
    <h1>Create your account</h1>
    <p>Your personal customer-verification link is generated immediately.</p>
    {alert}
    <form method="post" action="{u("/join")}">
      <input type="hidden" name="invite" value="{esc(invite)}">
      <label>Your name
        <input name="name" autocomplete="name" maxlength="100" required>
      </label>
      <label>Work email
        <input type="email" name="email" autocomplete="email" maxlength="200" required>
      </label>
      <label>Password
        <input type="password" name="password" autocomplete="new-password" minlength="10"
               required placeholder="10+ characters, with a number">
      </label>
      <button class="primary wide" type="submit">Create account and link</button>
    </form>
  </div>
</section>"""
    return shell("Employee enrollment", body)


def dashboard_page(
    member: dict,
    *,
    invite_token: str | None = None,
    notice: str | None = None,
) -> str:
    verifications = db.list_verifications(member)
    members = (
        db.list_members(member["organization_id"])
        if member["role"] == "admin"
        else []
    )
    verification_url = f"{settings.public_base}/v/{member['link_slug']}"
    notice_html = (
        f'<div class="alert success" role="status">{esc(notice)}</div>'
        if notice
        else ""
    )

    invite_html = ""
    if member["role"] == "admin":
        if invite_token:
            invite_url = f"{settings.public_base}/join?invite={quote(invite_token)}"
            invite_display = f"""
            <div class="copy-row">
              <input id="invite-link" readonly value="{esc(invite_url)}">
              <button class="secondary copy-button" data-copy="invite-link" type="button">Copy</button>
            </div>
            <p class="fine-print">Shown once. Rotate it whenever you need a new enrollment link.</p>
            """
        else:
            invite_display = (
                '<p class="muted">The current enrollment secret is not displayed. '
                "Rotate it to generate a new link.</p>"
            )
        enabled = bool(member["enrollment_enabled"])
        invite_html = f"""
        <section class="card admin-card">
          <div class="card-heading">
            <div><div class="eyebrow dark">Administrator</div>
            <h2>Employee enrollment</h2></div>
            <span class="status {("good" if enabled else "bad")}">
              {("Open" if enabled else "Closed")}
            </span>
          </div>
          {invite_display}
          <div class="button-row">
            <form method="post" action="{u("/admin/rotate-invite")}">
              <input type="hidden" name="csrf" value="{esc(member["csrf_token"])}">
              <button class="secondary" type="submit">Rotate enrollment link</button>
            </form>
            <form method="post" action="{u("/admin/enrollment")}">
              <input type="hidden" name="csrf" value="{esc(member["csrf_token"])}">
              <input type="hidden" name="enabled" value="{("0" if enabled else "1")}">
              <button class="secondary" type="submit">
                {("Close enrollment" if enabled else "Reopen enrollment")}
              </button>
            </form>
          </div>
        </section>
        """

    member_rows = ""
    if member["role"] == "admin":
        member_rows = "".join(
            f"""<tr>
              <td>{esc(item["name"])}</td>
              <td>{esc(item["email"])}</td>
              <td><span class="role">{esc(item["role"])}</span></td>
              <td><code>{esc(item["link_slug"])}</code></td>
            </tr>"""
            for item in members
        )
        member_rows = f"""
        <section class="card table-card">
          <div class="card-heading"><h2>Organization members</h2>
            <span class="muted">{len(members)} account(s)</span></div>
          <div class="table-scroll"><table>
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Link ID</th></tr></thead>
            <tbody>{member_rows}</tbody>
          </table></div>
        </section>"""

    if verifications:
        verification_rows = "".join(
            f"""<tr>
              <td><a href="{u(f"/results/{item['id']}")}">{esc(item["subject_name"])}</a></td>
              <td>{esc(item["employee_name"])}</td>
              <td><span class="status {status_class(item["status"])}">
                    {esc(status_label(item["status"]))}</span></td>
              <td>{esc(item["provider"].title())}</td>
              <td>{esc(item["created_at"][:16].replace("T", " "))} UTC</td>
            </tr>"""
            for item in verifications
        )
    else:
        verification_rows = (
            '<tr><td colspan="5" class="empty">No one has used this link yet.</td></tr>'
        )

    body = f"""
<section class="dashboard-head">
  <div>
    <div class="eyebrow dark">{esc(member["organization_name"])}</div>
    <h1>Welcome, {esc(member["name"].split()[0])}</h1>
    <p>Your account is active. Your verification link is ready to share.</p>
  </div>
  <span class="role large">{esc(member["role"])}</span>
</section>
{notice_html}
<section class="card link-card">
  <div>
    <div class="eyebrow dark">Your permanent verification link</div>
    <h2>One link. A fresh verification every time.</h2>
    <p>Send this to a customer by email, text, or QR code.</p>
  </div>
  <div class="copy-row">
    <input id="verification-link" readonly value="{esc(verification_url)}">
    <button class="primary copy-button" data-copy="verification-link" type="button">Copy link</button>
  </div>
  <div class="link-note">
    <strong>Plaid Sandbox active:</strong> new checks continue to Plaid's hosted test flow.
    Use only Plaid's published Sandbox identity.
  </div>
</section>
{invite_html}
<section class="card table-card">
  <div class="card-heading"><h2>Verification activity</h2>
    <span class="muted">Newest first</span></div>
  <div class="table-scroll"><table>
    <thead><tr><th>Customer</th><th>Employee</th><th>Outcome</th><th>Provider</th><th>Started</th></tr></thead>
    <tbody>{verification_rows}</tbody>
  </table></div>
</section>
{member_rows}
"""
    return shell("Dashboard", body, member=member)


def public_verification_page(member: dict, error: str | None = None) -> str:
    alert = error_box(error) if error else ""
    demo_note = (
        """
        <div class="demo-banner"><strong>Safe demo:</strong> use invented names and reserved
        email addresses only. This demo will not ask for an SSN or an ID image.</div>
        """
        if settings.provider == "demo"
        else """
        <div class="demo-banner live"><strong>Plaid Sandbox:</strong> after consent you will
        continue to Plaid's hosted identity-verification experience.</div>
        """
    )
    body = f"""
<section class="verify-page">
  <div class="verify-context">
    <div class="seal">L</div>
    <div class="eyebrow dark">Requested by</div>
    <h1>{esc(member["organization_name"])}</h1>
    <p>{esc(member["name"])} sent you this secure identity-verification link.</p>
    <div class="trust-list">
      <div><b>Encrypted connection</b><span>Protected in transit with HTTPS</span></div>
      <div><b>Minimal email</b><span>Sensitive results are never emailed</span></div>
      <div><b>Purpose limited</b><span>Identity and fraud-prevention signals only</span></div>
    </div>
  </div>
  <div class="card form-card verify-form">
    <div class="eyebrow dark">Before you continue</div>
    <h2>Confirm your details</h2>
    <p>The employee receives the outcome and a secure report link when processing finishes.</p>
    {demo_note}
    {alert}
    <form method="post" action="{u(f"/v/{member['link_slug']}")}">
      <label>Full legal name
        <input name="name" autocomplete="name" maxlength="120" required
               placeholder="Use sample data in demo mode">
      </label>
      <label>Email address <span class="optional">optional</span>
        <input type="email" name="email" autocomplete="email" maxlength="200"
               placeholder="sample@example.invalid">
      </label>
      <label class="honeypot" aria-hidden="true">Company website
        <input name="website" tabindex="-1" autocomplete="off">
      </label>
      <label class="consent">
        <input type="checkbox" name="consent" value="yes" required>
        <span>
          I consent to {esc(member["organization_name"])} using ProofLink and its
          verification provider to check the identity information I submit for
          identity confirmation and fraud prevention. I understand this is not a
          criminal-background check or consumer credit report.
        </span>
      </label>
      <button class="primary wide" type="submit">Start secure verification</button>
    </form>
    <p class="fine-print">Consent notice version {esc(CONSENT_VERSION)}</p>
  </div>
</section>"""
    return shell(
        f"Verify for {member['organization_name']}",
        body,
        public_flow=True,
    )


def demo_check_page(verification: dict, raw_token: str) -> str:
    body = f"""
<section class="narrow-page demo-check">
  <div class="card">
    <div class="demo-plaid-mark">P</div>
    <div class="eyebrow dark">Provider simulator</div>
    <h1>Run a sample identity check</h1>
    <p>
      Production would redirect to Plaid's hosted flow for data-source,
      document, selfie, and optional watchlist checks. This simulator stores no
      real identity document or national ID number.
    </p>
    <div class="sample-summary">
      <span>Customer</span><strong>{esc(verification["subject_name"])}</strong>
      <span>Requested by</span><strong>{esc(verification["employee_name"])}</strong>
      <span>Organization</span><strong>{esc(verification["organization_name"])}</strong>
    </div>
    <p class="choose-label">Choose a synthetic outcome to test the workflow:</p>
    <div class="scenario-grid">
      <form method="post" action="{u(f"/demo/{verification['id']}/complete")}">
        <input type="hidden" name="token" value="{esc(raw_token)}">
        <input type="hidden" name="scenario" value="success">
        <button class="scenario good-button" type="submit">
          <b>Pass</b><span>Records and document match</span>
        </button>
      </form>
      <form method="post" action="{u(f"/demo/{verification['id']}/complete")}">
        <input type="hidden" name="token" value="{esc(raw_token)}">
        <input type="hidden" name="scenario" value="pending_review">
        <button class="scenario warn-button" type="submit">
          <b>Needs review</b><span>Possible watchlist match</span>
        </button>
      </form>
      <form method="post" action="{u(f"/demo/{verification['id']}/complete")}">
        <input type="hidden" name="token" value="{esc(raw_token)}">
        <input type="hidden" name="scenario" value="failed">
        <button class="scenario bad-button" type="submit">
          <b>Unable to verify</b><span>Document did not pass</span>
        </button>
      </form>
    </div>
  </div>
</section>"""
    return shell("Provider simulator", body, public_flow=True)


def subject_complete_page(verification: dict) -> str:
    body = f"""
<section class="narrow-page completion">
  <div class="card center">
    <div class="success-ring">✓</div>
    <div class="eyebrow dark">Submission complete</div>
    <h1>You're all set.</h1>
    <p>
      The sample verification has finished. {esc(verification["employee_name"])}
      has been notified securely. You can close this window.
    </p>
    <div class="privacy-note">
      The outcome is not shown here because the organization may combine it with
      its own review process.
    </div>
  </div>
</section>"""
    return shell("Submission complete", body, public_flow=True)


def result_page(member: dict, verification: dict) -> str:
    result = verification.get("result") or {}
    steps = result.get("steps") or {}
    matches = result.get("field_matches") or {}
    score = result.get("trust_index_score")
    step_rows = "".join(
        f"""<div class="result-row"><span>{esc(key.replace("_", " ").title())}</span>
        <strong class="{status_class(str(value))}">{esc(status_label(str(value)))}</strong></div>"""
        for key, value in steps.items()
    ) or '<p class="muted">No step details are available yet.</p>'
    match_rows = "".join(
        f"""<div class="result-row"><span>{esc(key.replace("_", " ").title())}</span>
        <strong>{esc(value)}</strong></div>"""
        for key, value in matches.items()
    ) or '<p class="muted">No field-match details are available.</p>'
    score_html = (
        f'<div class="score"><b>{esc(score)}</b><span>Trust Index<br>out of 100</span></div>'
        if score is not None
        else ""
    )
    error_html = error_box(verification["error"]) if verification.get("error") else ""
    refresh_html = ""
    if verification["provider"] == "plaid" and verification["external_id"]:
        refresh_html = f"""
        <form method="post" action="{u(f"/results/{verification['id']}/refresh")}">
          <input type="hidden" name="csrf" value="{esc(member["csrf_token"])}">
          <button class="secondary" type="submit">Refresh from Plaid</button>
        </form>"""
    body = f"""
<section class="dashboard-head">
  <div>
    <a class="back-link" href="{u("/dashboard")}">← Back to dashboard</a>
    <div class="eyebrow dark">{esc(verification["organization_name"])}</div>
    <h1>{esc(verification["subject_name"])}</h1>
    <p>Requested by {esc(verification["employee_name"])} · {esc(verification["created_at"])}</p>
  </div>
  <span class="status result-status {status_class(verification["status"])}">
    {esc(status_label(verification["status"]))}
  </span>
</section>
{error_html}
<section class="result-grid">
  <div class="card">
    <div class="card-heading"><h2>Verification summary</h2>{score_html}</div>
    {step_rows}
  </div>
  <div class="card">
    <h2>Data-source matches</h2>
    {match_rows}
  </div>
</section>
<section class="card compliance-card">
  <div>
    <h2>Use restriction</h2>
    <p>
      Identity-verification signals are not a consumer report. Do not use this
      result as the sole basis for credit, employment, housing, insurance, or
      another eligibility decision.
    </p>
  </div>
  {refresh_html}
</section>
"""
    return shell("Verification result", body, member=member)


def validate_account_fields(form: dict[str, str]) -> tuple[str, str, str]:
    name = form.get("name", "").strip()
    email = form.get("email", "").strip().lower()
    password = form.get("password", "")
    if not name or len(name) > 100:
        raise ValueError("Enter a valid name.")
    if "@" not in email or len(email) > 200:
        raise ValueError("Enter a valid work email.")
    if not valid_password(password):
        raise ValueError(
            "Use a password with at least 10 characters, one letter, and one number."
        )
    return name, email, password


def notify_if_needed(verification: dict) -> None:
    if (
        verification["status"] not in TERMINAL_STATUSES
        or verification.get("employee_notified_at")
    ):
        return
    backend = send_result_notification(verification)
    db.mark_notified(verification["id"])
    db.audit(
        "verification.employee_notified",
        organization_id=verification["organization_id"],
        member_id=verification["member_id"],
        target_id=verification["id"],
        detail={"backend": backend},
    )


def sync_from_plaid(verification: dict) -> dict:
    if not verification.get("external_id"):
        raise PlaidError("This verification does not have a Plaid session ID.")
    payload = get_identity_verification(verification["external_id"])
    status = payload.get("status", "active")
    synced = db.complete_verification(
        verification["id"],
        status=status,
        result=normalize_result(payload),
    )
    notify_if_needed(synced)
    return db.get_verification(verification["id"]) or synced


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "plaid-identity-verification-demo",
        "provider": settings.provider,
        "plaid_configured": settings.plaid_ready,
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    member = current_member(request)
    if member:
        return RedirectResponse(u("/dashboard"), status_code=303)
    return HTMLResponse(landing())


@app.post("/organizations", response_class=HTMLResponse)
async def create_organization(request: Request):
    form = await form_data(request)
    try:
        name, email, password = validate_account_fields(form)
        organization_name = form.get("organization_name", "").strip()
        if not organization_name or len(organization_name) > 100:
            raise ValueError("Enter a valid organization name.")
        _, member, invite = db.create_organization(
            organization_name=organization_name,
            member_name=name,
            email=email,
            encoded_password=password_hash(password),
            request_ip=client_ip(request),
        )
    except ValueError as exc:
        return HTMLResponse(landing(str(exc)), status_code=400)
    except sqlite3.IntegrityError:
        return HTMLResponse(
            landing("That email already has an account. Sign in instead."),
            status_code=409,
        )

    raw_session, csrf = db.create_auth_session(
        member["id"],
        ttl_seconds=settings.session_ttl_seconds,
    )
    member["csrf_token"] = csrf
    response = HTMLResponse(
        dashboard_page(
            member,
            invite_token=invite,
            notice="Organization created. Copy the employee enrollment link below.",
        )
    )
    set_session_cookie(response, raw_session)
    return response


@app.get("/join", response_class=HTMLResponse)
def join_get(invite: str = ""):
    return HTMLResponse(join_page(invite))


@app.post("/join", response_class=HTMLResponse)
async def join_post(request: Request):
    form = await form_data(request)
    invite = form.get("invite", "").strip()
    try:
        name, email, password = validate_account_fields(form)
        if not invite:
            raise ValueError("The employee enrollment link is missing.")
        member = db.create_employee(
            invite_token=invite,
            member_name=name,
            email=email,
            encoded_password=password_hash(password),
            request_ip=client_ip(request),
        )
    except (ValueError, PermissionError) as exc:
        return HTMLResponse(join_page(invite, str(exc)), status_code=400)
    except sqlite3.IntegrityError:
        return HTMLResponse(
            join_page(invite, "That email already has an account. Sign in instead."),
            status_code=409,
        )

    raw_session, csrf = db.create_auth_session(
        member["id"],
        ttl_seconds=settings.session_ttl_seconds,
    )
    member["csrf_token"] = csrf
    response = HTMLResponse(
        dashboard_page(
            member,
            notice="Account created. Your personal verification link is ready.",
        )
    )
    set_session_cookie(response, raw_session)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if current_member(request):
        return RedirectResponse(u("/dashboard"), status_code=303)
    next_path = request.query_params.get("next", "")
    allowed_prefix = u("/results/")
    if next_path and not next_path.startswith(allowed_prefix):
        next_path = ""
    return HTMLResponse(login_page(next_path=next_path))


@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request):
    form = await form_data(request)
    email = form.get("email", "").strip().lower()
    password = form.get("password", "")
    next_path = form.get("next", "")
    if next_path and not next_path.startswith(u("/results/")):
        next_path = ""
    member = db.get_member_by_email(email)
    if (
        not member
        or not member["active"]
        or not password_verify(password, member["password_hash"])
    ):
        db.audit("auth.failed", request_ip=client_ip(request), detail={"email": email})
        return HTMLResponse(
            login_page(
                "Email or password is incorrect.",
                next_path=next_path,
            ),
            status_code=401,
        )
    raw_session, _ = db.create_auth_session(
        member["id"],
        ttl_seconds=settings.session_ttl_seconds,
    )
    response = RedirectResponse(next_path or u("/dashboard"), status_code=303)
    set_session_cookie(response, raw_session)
    db.audit(
        "auth.succeeded",
        organization_id=member["organization_id"],
        member_id=member["id"],
        request_ip=client_ip(request),
    )
    return response


@app.post("/logout")
async def logout(request: Request):
    member = current_member(request)
    form = await form_data(request)
    if member and not csrf_valid(member, form):
        raise HTTPException(403, "Invalid CSRF token.")
    db.delete_auth_session(request.cookies.get(COOKIE_NAME))
    response = RedirectResponse(u("/"), status_code=303)
    clear_session_cookie(response)
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    member = current_member(request)
    if not member:
        return RedirectResponse(u("/login"), status_code=303)
    return HTMLResponse(dashboard_page(member))


@app.post("/admin/rotate-invite", response_class=HTMLResponse)
async def rotate_invite(request: Request):
    member = current_member(request)
    if not member:
        return RedirectResponse(u("/login"), status_code=303)
    form = await form_data(request)
    if member["role"] != "admin" or not csrf_valid(member, form):
        raise HTTPException(403, "Administrator access required.")
    invite = db.rotate_join_token(member["organization_id"])
    db.audit(
        "organization.invite_rotated",
        organization_id=member["organization_id"],
        member_id=member["id"],
        request_ip=client_ip(request),
    )
    member = db.member_for_session(request.cookies.get(COOKIE_NAME)) or member
    return HTMLResponse(
        dashboard_page(
            member,
            invite_token=invite,
            notice="New employee enrollment link generated.",
        )
    )


@app.post("/admin/enrollment", response_class=HTMLResponse)
async def enrollment(request: Request):
    member = current_member(request)
    if not member:
        return RedirectResponse(u("/login"), status_code=303)
    form = await form_data(request)
    if member["role"] != "admin" or not csrf_valid(member, form):
        raise HTTPException(403, "Administrator access required.")
    enabled = form.get("enabled") == "1"
    db.set_enrollment(member["organization_id"], enabled)
    db.audit(
        "organization.enrollment_changed",
        organization_id=member["organization_id"],
        member_id=member["id"],
        request_ip=client_ip(request),
        detail={"enabled": enabled},
    )
    member = db.member_for_session(request.cookies.get(COOKIE_NAME)) or member
    return HTMLResponse(
        dashboard_page(
            member,
            notice=f"Employee enrollment {'opened' if enabled else 'closed'}.",
        )
    )


@app.get("/v/{link_slug}", response_class=HTMLResponse)
def verification_link(link_slug: str):
    member = db.get_member_by_link(link_slug)
    if not member:
        raise HTTPException(404, "This verification link is inactive or invalid.")
    return HTMLResponse(public_verification_page(member))


@app.post("/v/{link_slug}", response_class=HTMLResponse)
async def start_verification(link_slug: str, request: Request):
    member = db.get_member_by_link(link_slug)
    if not member:
        raise HTTPException(404, "This verification link is inactive or invalid.")
    form = await form_data(request)
    if form.get("website"):
        raise HTTPException(400, "Invalid submission.")
    subject_name = form.get("name", "").strip()
    subject_email = form.get("email", "").strip().lower() or None
    if not subject_name or len(subject_name) > 120:
        return HTMLResponse(
            public_verification_page(member, "Enter a valid legal name."),
            status_code=400,
        )
    if subject_email and ("@" not in subject_email or len(subject_email) > 200):
        return HTMLResponse(
            public_verification_page(member, "Enter a valid email address."),
            status_code=400,
        )
    if form.get("consent") != "yes":
        return HTMLResponse(
            public_verification_page(member, "Consent is required to continue."),
            status_code=400,
        )
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(
        timespec="seconds"
    )
    if db.recent_link_attempts(member["id"], since) >= 50:
        return HTMLResponse(
            public_verification_page(
                member,
                "This link reached its daily demo limit. Contact the employee.",
            ),
            status_code=429,
        )

    verification, completion_token = db.create_verification(
        member=member,
        provider=settings.provider,
        subject_name=subject_name,
        subject_email=subject_email,
        consent_version=CONSENT_VERSION,
        request_ip=client_ip(request),
    )
    if settings.provider == "demo":
        demo_path = u("/demo/" + verification["id"])
        location = f"{demo_path}?token={quote(completion_token)}"
        return RedirectResponse(location, status_code=303)

    try:
        plaid = create_identity_verification(
            client_user_id=verification["client_user_id"],
            subject_email=subject_email,
        )
        db.set_external_verification(
            verification["id"],
            external_id=plaid["id"],
            status=plaid.get("status", "active"),
        )
        return RedirectResponse(plaid["shareable_url"], status_code=303)
    except PlaidError as exc:
        db.set_verification_error(verification["id"], str(exc))
        LOGGER.exception("Could not start Plaid verification")
        return HTMLResponse(
            public_verification_page(
                member,
                "The verification provider is temporarily unavailable. "
                "No identity data was submitted. Please try again later.",
            ),
            status_code=502,
        )


@app.get("/demo/{verification_id}", response_class=HTMLResponse)
def demo_check(verification_id: str, token: str = ""):
    verification = db.get_verification(verification_id)
    if (
        not verification
        or verification["provider"] != "demo"
        or not db.completion_token_valid(verification_id, token)
    ):
        raise HTTPException(404, "This sample verification is invalid.")
    if verification["status"] in TERMINAL_STATUSES:
        return HTMLResponse(subject_complete_page(verification))
    return HTMLResponse(demo_check_page(verification, token))


@app.post("/demo/{verification_id}/complete", response_class=HTMLResponse)
async def complete_demo(verification_id: str, request: Request):
    form = await form_data(request)
    raw_token = form.get("token", "")
    scenario = form.get("scenario", "")
    verification = db.get_verification(verification_id)
    if (
        not verification
        or verification["provider"] != "demo"
        or not db.completion_token_valid(verification_id, raw_token)
        or scenario not in {"success", "pending_review", "failed"}
    ):
        raise HTTPException(404, "This sample verification is invalid.")
    if verification["status"] not in TERMINAL_STATUSES:
        if scenario == "success":
            result = {
                "status": "success",
                "steps": {
                    "verify_sms": "success",
                    "kyc_check": "success",
                    "documentary_verification": "success",
                    "selfie_check": "success",
                    "watchlist_screening": "success",
                    "risk_check": "success",
                },
                "field_matches": {
                    "name": "match",
                    "date_of_birth": "match",
                    "id_number": "match",
                    "address": "match",
                    "phone_number": "match",
                },
                "document_status": "success",
                "selfie_status": "success",
                "trust_index_score": 92,
            }
        elif scenario == "pending_review":
            result = {
                "status": "pending_review",
                "steps": {
                    "verify_sms": "success",
                    "kyc_check": "success",
                    "documentary_verification": "success",
                    "selfie_check": "success",
                    "watchlist_screening": "pending_review",
                    "risk_check": "success",
                },
                "field_matches": {
                    "name": "match",
                    "date_of_birth": "match",
                    "address": "match",
                },
                "document_status": "success",
                "selfie_status": "success",
                "trust_index_score": 71,
            }
        else:
            result = {
                "status": "failed",
                "steps": {
                    "verify_sms": "success",
                    "kyc_check": "failed",
                    "documentary_verification": "failed",
                    "selfie_check": "not_applicable",
                    "watchlist_screening": "not_applicable",
                    "risk_check": "success",
                },
                "field_matches": {
                    "name": "partial_match",
                    "date_of_birth": "no_match",
                },
                "document_status": "failed",
                "selfie_status": None,
                "trust_index_score": 34,
            }
        verification = db.complete_verification(
            verification_id,
            status=scenario,
            result=result,
        )
        db.audit(
            "verification.demo_completed",
            organization_id=verification["organization_id"],
            member_id=verification["member_id"],
            target_id=verification_id,
            request_ip=client_ip(request),
            detail={"scenario": scenario},
        )
        try:
            notify_if_needed(verification)
        except Exception:  # noqa: BLE001 - result must survive notification failure
            LOGGER.exception("Could not notify employee")
    verification = db.get_verification(verification_id) or verification
    return HTMLResponse(subject_complete_page(verification))


@app.get("/results/{verification_id}", response_class=HTMLResponse)
def result(verification_id: str, request: Request):
    member = current_member(request)
    if not member:
        return RedirectResponse(
            f"{u('/login')}?next={quote(u(f'/results/{verification_id}'))}",
            status_code=303,
        )
    verification = db.get_verification(verification_id)
    if not verification or verification["organization_id"] != member["organization_id"]:
        raise HTTPException(404, "Verification not found.")
    if member["role"] != "admin" and verification["member_id"] != member["id"]:
        raise HTTPException(404, "Verification not found.")
    db.audit(
        "verification.viewed",
        organization_id=member["organization_id"],
        member_id=member["id"],
        target_id=verification_id,
        request_ip=client_ip(request),
    )
    return HTMLResponse(result_page(member, verification))


@app.post("/results/{verification_id}/refresh", response_class=HTMLResponse)
async def refresh_result(verification_id: str, request: Request):
    member = current_member(request)
    if not member:
        return RedirectResponse(u("/login"), status_code=303)
    form = await form_data(request)
    if not csrf_valid(member, form):
        raise HTTPException(403, "Invalid CSRF token.")
    verification = db.get_verification(verification_id)
    if not verification or verification["organization_id"] != member["organization_id"]:
        raise HTTPException(404, "Verification not found.")
    if member["role"] != "admin" and verification["member_id"] != member["id"]:
        raise HTTPException(404, "Verification not found.")
    try:
        verification = sync_from_plaid(verification)
    except PlaidError as exc:
        db.set_verification_error(verification_id, str(exc))
        verification = db.get_verification(verification_id) or verification
    return HTMLResponse(result_page(member, verification))


@app.post("/webhooks/plaid")
async def plaid_webhook(request: Request):
    raw_body = await request.body()
    try:
        verify_webhook(
            raw_body,
            request.headers.get("Plaid-Verification"),
        )
        payload = json.loads(raw_body)
    except (PlaidError, json.JSONDecodeError) as exc:
        LOGGER.warning("Rejected Plaid webhook: %s", exc)
        return JSONResponse({"ok": False}, status_code=401)

    external_id = payload.get("identity_verification_id")
    if not external_id:
        return {"ok": True, "ignored": True}
    verification = db.get_verification_by_external(str(external_id))
    if not verification:
        # Return 200 so Plaid does not repeatedly retry an event that belongs
        # to another environment or a session created outside this demo.
        return {"ok": True, "ignored": True}
    try:
        synced = sync_from_plaid(verification)
    except PlaidError:
        LOGGER.exception("Could not sync Plaid verification after webhook")
        return JSONResponse({"ok": False}, status_code=503)
    db.audit(
        "verification.plaid_webhook",
        organization_id=synced["organization_id"],
        member_id=synced["member_id"],
        target_id=synced["id"],
        detail={
            "webhook_type": payload.get("webhook_type"),
            "webhook_code": payload.get("webhook_code"),
        },
    )
    return {"ok": True}
