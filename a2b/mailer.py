"""Outbound email. Today that means password resets and invites.

Three transports, picked by `A2B_EMAIL_BACKEND` or inferred from what is
configured:

    smtp     any SMTP server, via the standard library
    ses      AWS SES, via boto3
    outbox   writes the message under the data directory and to stderr

The outbox is the development default and also the fallback when a real
transport raises. That is deliberate. A reset link that fails to send is a
person locked out of their account, and the failure would otherwise be
invisible: the endpoint cannot report it to the caller without telling an
attacker which addresses have accounts. Writing it somewhere recoverable means
an operator can still get the person in while the transport is fixed.

boto3 is imported inside the SES path so it stays an optional dependency; the
SMTP path is standard library only, which keeps this working on a box with no
compiler.
"""
from __future__ import annotations

import os
import secrets
import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from .config import DATA_DIR, PUBLIC_BASE

BRAND = "Lenni A2B"


def from_address() -> str:
    return os.environ.get("A2B_EMAIL_FROM", "no-reply@lenni.io")


def site_base() -> str:
    """The origin to build links against. Falls back to the configured public
    base so a deployment that already knows where it answers does not need a
    second variable saying the same thing."""
    return (os.environ.get("A2B_SITE_BASE", "").strip().rstrip("/")
            or PUBLIC_BASE
            or "http://127.0.0.1:8000")


def backend() -> str:
    explicit = (os.environ.get("A2B_EMAIL_BACKEND") or "").strip().lower()
    if explicit in {"smtp", "ses", "outbox"}:
        return explicit
    if os.environ.get("A2B_SMTP_HOST"):
        return "smtp"
    if os.environ.get("A2B_EMAIL_FROM") and os.environ.get("AWS_REGION"):
        return "ses"
    return "outbox"


def _outbox_dir() -> Path:
    path = DATA_DIR / "outbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _send_outbox(to: str, subject: str, text: str, html: str) -> None:
    # Microseconds, then a random tie-break. Asking for two resets in a row is
    # a normal thing to do, and a stamp only good to the second would have the
    # second email overwrite the first — losing the one that still works. The
    # timestamp leads so that sorting these filenames still orders them by when
    # they were sent, which is how anyone reading an outbox expects it to work.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    safe = to.replace("@", "_at_").replace("/", "_")
    path = _outbox_dir() / f"{stamp}-{secrets.token_hex(3)}-{safe}.txt"
    path.write_text(f"To: {to}\nSubject: {subject}\n\n{text}\n", encoding="utf-8")
    print(f"[a2b-mail:outbox] {subject} -> {to} ({path})", file=sys.stderr, flush=True)


def _message(to: str, subject: str, text: str, html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((BRAND, from_address()))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def _send_smtp(to: str, subject: str, text: str, html: str) -> None:
    host = os.environ.get("A2B_SMTP_HOST", "")
    port = int(os.environ.get("A2B_SMTP_PORT", "587"))
    user = os.environ.get("A2B_SMTP_USER")
    password = os.environ.get("A2B_SMTP_PASSWORD")
    use_tls = (os.environ.get("A2B_SMTP_TLS", "true").lower() != "false")

    with smtplib.SMTP(host, port, timeout=20) as server:
        if use_tls:
            server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(_message(to, subject, text, html))


def _send_ses(to: str, subject: str, text: str, html: str) -> None:
    import boto3  # optional dependency; only this path needs it

    boto3.client("ses", region_name=os.environ.get("AWS_REGION", "us-east-2")).send_email(
        Source=from_address(),
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": text, "Charset": "UTF-8"},
                "Html": {"Data": html, "Charset": "UTF-8"},
            },
        },
    )


def send(to: str, subject: str, text: str, html: str) -> dict:
    """Deliver a message. Never raises — see the module docstring."""
    chosen = backend()
    try:
        if chosen == "smtp":
            _send_smtp(to, subject, text, html)
        elif chosen == "ses":
            _send_ses(to, subject, text, html)
        else:
            _send_outbox(to, subject, text, html)
        return {"sent": True, "backend": chosen}
    except Exception as exc:  # noqa: BLE001 — a send failure must not fail the request
        print(f"[a2b-mail] {chosen} failed: {exc}", file=sys.stderr, flush=True)
        _send_outbox(to, subject, text, html)
        return {"sent": False, "backend": "outbox", "error": str(exc)}


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------

def _shell(heading: str, body: str) -> str:
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f4f6fb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6fb;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border-radius:12px;padding:32px;">
        <tr><td style="font-size:20px;font-weight:600;color:#203048;padding-bottom:12px;">{heading}</td></tr>
        <tr><td style="font-size:15px;line-height:1.6;color:#3c4657;">{body}</td></tr>
        <tr><td style="padding-top:28px;font-size:12px;color:#8892a4;border-top:1px solid #e4e8ee;">
          {BRAND} &middot; If you were not expecting this email you can ignore it.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def send_password_reset(to: str, token: str) -> dict:
    link = f"{site_base()}/reset-password?token={token}"
    text = (
        "Someone asked to reset the password for your Lenni A2B account.\n\n"
        f"{link}\n\n"
        "The link works once and expires in an hour.\n\n"
        "If this was not you, nothing has changed and you can ignore this email."
    )
    html = _shell(
        "Reset your password",
        "<p>Someone asked to reset the password for your Lenni A2B account.</p>"
        f'<p style="padding:16px 0;"><a href="{link}" '
        'style="background:#209D76;color:#ffffff;padding:12px 22px;border-radius:8px;'
        'text-decoration:none;font-weight:600;display:inline-block;">Choose a new password</a></p>'
        "<p>The link works once and expires in an hour.</p>"
        "<p>If this was not you, nothing has changed and you can ignore this email.</p>",
    )
    return send(to, "Reset your Lenni A2B password", text, html)


def send_reset_for_invitee(to: str) -> dict:
    """Someone asked to reset a password for an address that was invited to a
    workspace but never signed up.

    Sending nothing would be correct and useless: they would sit waiting for an
    email that is never coming, because they have no account to reset. Telling
    them to sign up instead resolves it without confirming anything an attacker
    could not already learn by trying to sign up with the address themselves.
    """
    link = f"{site_base()}/signup"
    text = (
        "Someone asked to reset a password for this address.\n\n"
        "There is no Lenni A2B account on it yet, but you have been invited to a "
        "workspace — create an account with this address and the invitation "
        "becomes access straight away.\n\n"
        f"{link}\n\n"
        "If this was not you, you can ignore this email."
    )
    html = _shell(
        "Create your account",
        "<p>Someone asked to reset a password for this address.</p>"
        "<p>There is no account on it yet, but you have been invited to a workspace. "
        "Create an account with this address and the invitation becomes access "
        "straight away.</p>"
        f'<p style="padding:16px 0;"><a href="{link}" '
        'style="background:#209D76;color:#ffffff;padding:12px 22px;border-radius:8px;'
        'text-decoration:none;font-weight:600;display:inline-block;">Create an account</a></p>'
        "<p>If this was not you, you can ignore this email.</p>",
    )
    return send(to, "Create your Lenni A2B account", text, html)

