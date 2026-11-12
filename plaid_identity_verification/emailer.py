"""Outcome-only email notifications.

The email intentionally excludes SSNs, dates of birth, addresses, document
images, and biometric media. Those never belong in an inbox.
"""

from __future__ import annotations

import html
import sys

from config import settings


STATUS_LABELS = {
    "success": "Passed",
    "failed": "Could not verify",
    "pending_review": "Needs review",
    "canceled": "Canceled",
    "expired": "Expired",
    "error": "System error",
}


def _outbox_write(
    recipient: str,
    subject: str,
    text: str,
    html_body: str,
) -> None:
    outbox = settings.data_dir / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    safe = recipient.replace("@", "_at_").replace("/", "_")
    stamp = __import__("time").strftime("%Y%m%d-%H%M%S")
    (outbox / f"{stamp}-{safe}.txt").write_text(
        f"To: {recipient}\nSubject: {subject}\n\n{text}\n\n{html_body}",
        encoding="utf-8",
    )


def send_result_notification(verification: dict) -> str:
    recipient = verification["employee_email"]
    result = verification.get("result") or {}
    label = STATUS_LABELS.get(
        verification["status"],
        verification["status"].replace("_", " ").title(),
    )
    subject_name = verification["subject_name"]
    result_url = f"{settings.public_base}/results/{verification['id']}"
    trust_score = result.get("trust_index_score")
    trust_line = f" · Trust Index {trust_score}/100" if trust_score is not None else ""
    subject = f"Identity verification complete — {subject_name} — {label}"

    text = (
        f"Hello {verification['employee_name']},\n\n"
        f"The identity verification you initiated for {subject_name} has finished.\n"
        f"Outcome: {label}{trust_line}\n\n"
        f"Sign in to view the secure result:\n{result_url}\n\n"
        "This email intentionally does not contain sensitive identity data.\n"
        "Identity-verification signals are not a consumer report and must not be "
        "used as the sole basis for a credit, employment, housing, or insurance decision.\n"
    )

    safe_name = html.escape(subject_name)
    safe_employee = html.escape(verification["employee_name"])
    safe_label = html.escape(label)
    html_body = f"""<!doctype html>
<html><body style="margin:0;background:#eef4f2;font-family:Arial,sans-serif;color:#152521">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center" style="padding:36px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="max-width:560px;background:#fff;border-radius:18px;overflow:hidden">
<tr><td style="background:#0c3d32;color:#fff;padding:26px 30px">
  <div style="font-size:20px;font-weight:700">ProofLink</div>
  <div style="font-size:13px;color:#bbd5ce;margin-top:4px">Identity verification update</div>
</td></tr>
<tr><td style="padding:30px">
  <p style="margin:0 0 18px">Hello {safe_employee},</p>
  <p style="margin:0 0 18px">The verification for <strong>{safe_name}</strong> has finished.</p>
  <div style="background:#eef8f4;border-left:4px solid #18a978;padding:16px;margin:0 0 24px">
    <strong>{safe_label}</strong>{html.escape(trust_line)}
  </div>
  <a href="{html.escape(result_url)}"
     style="display:inline-block;background:#18a978;color:#fff;text-decoration:none;
            padding:13px 20px;border-radius:9px;font-weight:700">View secure result</a>
  <p style="margin:24px 0 0;color:#62726d;font-size:12px;line-height:1.55">
    Sensitive identity data is intentionally excluded from email. Sign-in is required.
    This is an identity-verification signal, not a consumer report.
  </p>
</td></tr>
</table></td></tr></table></body></html>"""

    # Reserved example domains are always written to the local outbox so
    # automated tests can never send mail to a real person.
    if recipient.endswith((".invalid", ".example", "@example.com")):
        _outbox_write(recipient, subject, text, html_body)
        return "console"

    if settings.email_backend == "ses":
        try:
            import boto3

            boto3.client("ses", region_name=settings.aws_region).send_email(
                Source=settings.email_from,
                Destination={"ToAddresses": [recipient]},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": text, "Charset": "UTF-8"},
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                    },
                },
            )
            return "ses"
        except Exception as exc:  # noqa: BLE001 - retain notification in outbox
            print(f"[piv-email] SES failed: {exc}", file=sys.stderr, flush=True)

    _outbox_write(recipient, subject, text, html_body)
    return "console"
