"""Small Plaid Identity Verification client.

Only normalized verification signals are returned to the app. Raw SSNs,
document images, selfie media, and the full Plaid payload are deliberately not
persisted by this demo.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import jwt
import requests

from config import settings


class PlaidError(RuntimeError):
    pass


_JWK_CACHE: dict[str, Any] = {}


def _post(
    path: str,
    payload: dict[str, Any],
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "PLAID-CLIENT-ID": settings.plaid_client_id,
        "PLAID-SECRET": settings.plaid_secret,
        "Plaid-Version": "2020-09-14",
    }
    try:
        response = requests.post(
            f"{base_url or settings.plaid_base_url}{path}",
            headers=headers,
            json=payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise PlaidError(f"Plaid is temporarily unreachable: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise PlaidError(
            f"Plaid returned a non-JSON response ({response.status_code})."
        ) from exc

    if response.status_code >= 400:
        code = body.get("error_code") or body.get("error_type") or "PLAID_ERROR"
        message = body.get("error_message") or "Plaid rejected the request."
        raise PlaidError(f"{code}: {message}")
    return body


def create_identity_verification(
    *,
    client_user_id: str,
    subject_email: str | None,
) -> dict[str, Any]:
    if not settings.plaid_ready:
        raise PlaidError(
            "Plaid mode is enabled, but PLAID_CLIENT_ID, PLAID_SECRET, or "
            "PLAID_IDV_TEMPLATE_ID is missing."
        )
    payload: dict[str, Any] = {
        "client_user_id": client_user_id,
        "template_id": settings.plaid_template_id,
        "is_shareable": True,
        # Our page records product-specific consent, while Plaid still shows
        # its own terms. Do not claim the user accepted Plaid's terms for them.
        "gave_consent": False,
    }
    if subject_email:
        payload["user"] = {"email_address": subject_email}
    body = _post("/identity_verification/create", payload)
    if not body.get("id") or not body.get("shareable_url"):
        raise PlaidError("Plaid did not return an IDV id and shareable URL.")
    return body


def get_identity_verification(external_id: str) -> dict[str, Any]:
    return _post(
        "/identity_verification/get",
        {"identity_verification_id": external_id},
    )


def normalize_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep useful signals while excluding raw PII and biometric media."""
    steps = payload.get("steps") or {}
    kyc = payload.get("kyc_check") or {}
    document = payload.get("documentary_verification") or {}
    selfie = payload.get("selfie_check") or {}
    risk = payload.get("risk_check") or {}

    field_matches = {}
    for field in (
        "name",
        "date_of_birth",
        "id_number",
        "address",
        "phone_number",
    ):
        value = kyc.get(field)
        if isinstance(value, dict) and value.get("summary"):
            field_matches[field] = value["summary"]

    return {
        "status": payload.get("status", "active"),
        "steps": {
            key: steps.get(key)
            for key in (
                "accept_tos",
                "verify_sms",
                "kyc_check",
                "documentary_verification",
                "selfie_check",
                "watchlist_screening",
                "risk_check",
            )
            if key in steps
        },
        "field_matches": field_matches,
        "document_status": document.get("status"),
        "selfie_status": selfie.get("status"),
        "trust_index_score": risk.get("trust_index_score"),
        "watchlist_screening_id": payload.get("watchlist_screening_id"),
        "completed_at": payload.get("completed_at"),
        "template": payload.get("template"),
    }


def _jwk_for(kid: str) -> Any:
    cached = _JWK_CACHE.get(kid)
    if cached is not None:
        return cached
    response = _post(
        "/webhook_verification_key/get",
        {"key_id": kid},
        # Use the same Plaid environment as the credentials. A Sandbox secret
        # is rejected by the production host.
        base_url=settings.plaid_base_url,
    )
    key_data = response.get("key")
    if not isinstance(key_data, dict) or key_data.get("kid") != kid:
        raise PlaidError("Plaid returned an invalid webhook verification key.")
    key = jwt.PyJWK.from_dict(key_data).key
    _JWK_CACHE[kid] = key
    return key


def verify_webhook(raw_body: bytes, verification_header: str | None) -> dict[str, Any]:
    if not verification_header:
        raise PlaidError("Missing Plaid-Verification header.")
    try:
        unverified = jwt.get_unverified_header(verification_header)
    except jwt.PyJWTError as exc:
        raise PlaidError("Malformed Plaid webhook JWT.") from exc
    if unverified.get("alg") != "ES256" or not unverified.get("kid"):
        raise PlaidError("Unexpected Plaid webhook signing algorithm or key.")

    try:
        claims = jwt.decode(
            verification_header,
            _jwk_for(unverified["kid"]),
            algorithms=["ES256"],
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        raise PlaidError("Invalid Plaid webhook signature.") from exc

    issued_at = claims.get("iat")
    if not isinstance(issued_at, (int, float)):
        raise PlaidError("Plaid webhook is missing its issue time.")
    if abs(time.time() - float(issued_at)) > 300:
        raise PlaidError("Plaid webhook is stale or has an invalid clock.")

    expected_hash = claims.get("request_body_sha256")
    actual_hash = hashlib.sha256(raw_body).hexdigest()
    if not isinstance(expected_hash, str) or not hmac.compare_digest(
        expected_hash,
        actual_hash,
    ):
        raise PlaidError("Plaid webhook body hash does not match.")
    return claims
