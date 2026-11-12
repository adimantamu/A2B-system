"""Password, token, and CSRF helpers for the demo."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


PBKDF2_ROUNDS = 310_000


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ROUNDS,
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(PBKDF2_ROUNDS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def password_verify(password: str, encoded: str) -> bool:
    try:
        scheme, rounds_text, salt_text, expected_text = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(expected_text)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(rounds_text),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def short_token(length: int = 9) -> str:
    return secrets.token_urlsafe(length).replace("-", "").replace("_", "")


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def valid_password(password: str) -> bool:
    return (
        len(password) >= 10
        and any(ch.isalpha() for ch in password)
        and any(ch.isdigit() for ch in password)
    )
