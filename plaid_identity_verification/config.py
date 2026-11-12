"""Configuration for the isolated Plaid Identity Verification demo."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    public_base: str
    base_path: str
    provider: str
    cookie_secure: bool
    session_ttl_seconds: int
    plaid_env: str
    plaid_client_id: str
    plaid_secret: str
    plaid_template_id: str
    email_backend: str
    email_from: str
    aws_region: str

    @property
    def database_path(self) -> Path:
        return self.data_dir / "demo.sqlite3"

    @property
    def plaid_base_url(self) -> str:
        if self.plaid_env == "production":
            return "https://production.plaid.com"
        return "https://sandbox.plaid.com"

    @property
    def plaid_ready(self) -> bool:
        return bool(
            self.plaid_client_id
            and self.plaid_secret
            and self.plaid_template_id
        )


def load_settings() -> Settings:
    base_path = os.getenv("PIV_BASE_PATH", "").strip()
    if base_path and not base_path.startswith("/"):
        base_path = f"/{base_path}"
    base_path = base_path.rstrip("/")

    provider = os.getenv("PIV_PROVIDER", "demo").strip().lower()
    if provider not in {"demo", "plaid"}:
        raise RuntimeError("PIV_PROVIDER must be either 'demo' or 'plaid'")

    plaid_env = os.getenv("PLAID_ENV", "sandbox").strip().lower()
    if plaid_env not in {"sandbox", "production"}:
        raise RuntimeError("PLAID_ENV must be either 'sandbox' or 'production'")

    default_base = f"http://127.0.0.1:8011{base_path}"
    return Settings(
        data_dir=Path(
            os.getenv(
                "PIV_DATA_DIR",
                "/var/lib/plaid_identity_verification",
            )
        ),
        public_base=os.getenv("PIV_PUBLIC_BASE", default_base).rstrip("/"),
        base_path=base_path,
        provider=provider,
        cookie_secure=_bool("PIV_COOKIE_SECURE", True),
        session_ttl_seconds=int(
            os.getenv("PIV_SESSION_TTL_SECONDS", str(12 * 60 * 60))
        ),
        plaid_env=plaid_env,
        plaid_client_id=os.getenv("PLAID_CLIENT_ID", "").strip(),
        plaid_secret=os.getenv("PLAID_SECRET", "").strip(),
        plaid_template_id=os.getenv("PLAID_IDV_TEMPLATE_ID", "").strip(),
        email_backend=os.getenv(
            "PIV_EMAIL_BACKEND",
            os.getenv("A2B_EMAIL_BACKEND", "console"),
        ).strip().lower(),
        email_from=os.getenv(
            "PIV_EMAIL_FROM",
            os.getenv("A2B_EMAIL_FROM", "no-reply@lenni.io"),
        ).strip(),
        aws_region=os.getenv(
            "AWS_REGION",
            os.getenv("AWS_DEFAULT_REGION", "us-east-2"),
        ).strip(),
    )


settings = load_settings()
