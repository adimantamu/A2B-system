"""Paths and environment settings."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = ROOT / "content"
TEMPLATE_DIR = CONTENT_DIR / "templates"
CONSOLE_DIR = ROOT / "console"
ADMIN_DIR = ROOT / "internal"
PUBLIC_DIR = ROOT / "public"

DATA_DIR = Path(os.environ.get("A2B_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("A2B_DB_PATH", DATA_DIR / "a2b.db"))
PACKAGE_DIR = Path(os.environ.get("A2B_PACKAGE_DIR", DATA_DIR / "packages"))

def _origin(value: str) -> str:
    """Normalise a configured host into a real origin.

    Everything we hand to a customer's AI has to be fetchable, and a bare
    `host/path` is not: agents that were told to open `api.lenni.io/mcp?...`
    treated it as prose rather than a URL. Anything without a scheme gets
    https, and the trailing slash goes so joins stay predictable.
    """
    v = (value or "").strip().rstrip("/")
    if not v:
        return ""
    if not v.startswith(("http://", "https://")):
        v = f"https://{v}"
    return v


# Where this deployment answers on the public internet. Entry prompts and the
# agent manifest carry these into the customer's AI, so they have to be the
# externally reachable host, not localhost, in anything but development.
MCP_BASE = _origin(os.environ.get("A2B_MCP_BASE", "api.lenni.io/mcp"))

# The origin the public doors live on. Defaults to whatever MCP_BASE points at,
# so a single A2B_MCP_BASE keeps configuring the whole deployment.
PUBLIC_BASE = _origin(os.environ.get("A2B_PUBLIC_BASE", "")) or (
    MCP_BASE[: -len("/mcp")] if MCP_BASE.endswith("/mcp") else MCP_BASE
)

# Where a bank's fixed start links answer. They are pasted onto the bank's own
# site once, so they name the host we mean to keep rather than whichever
# environment the editor happened to be signed in to.
LIVE_BASE = _origin(os.environ.get("A2B_LIVE_BASE", "")) or PUBLIC_BASE


def live_vendors() -> frozenset[str]:
    """Organisations with no `vendor` row whose live links staff may publish.

    The demo workspace's banks are fixture data, so there is no membership to
    check against. Everything not named here needs a real vendor. Without the
    list, editing a scanned prospect in the workspace would put a prompt in a
    real bank's name behind a public lenni.io address. Read per call, like
    `staff_emails`.
    """
    raw = os.environ.get("A2B_LIVE_VENDORS", "")
    return frozenset(s.strip().lower() for s in raw.split(",") if s.strip())


def staff_emails() -> frozenset[str]:
    """Who may open the internal portal, read at call time rather than import.

    Deliberately not "anyone at our domain". Signup does not verify an address,
    so a rule read off the email would let a stranger type `someone@lenni.io`
    and be handed every bank's data. This list is set where the service is
    started, which is somewhere only we can write.

    Read per call so the answer follows the environment. It is a handful of
    string comparisons, and having to restart the API to grant access during an
    incident is a worse trade than the lookup.
    """
    raw = os.environ.get("A2B_STAFF_EMAILS", "")
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


# The guardrail is not configurable per vendor. See docs/MODEL_RULES.md.
GUARDRAIL = (
    "This is preparation only: not a credit decision, not a rate quote, and not "
    "a commitment to lend. The vendor makes all decisions."
)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PACKAGE_DIR.mkdir(parents=True, exist_ok=True)
