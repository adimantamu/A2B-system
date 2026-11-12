"""SQLite persistence for vendors, skills, sections, profiles and sessions.

The registry side of the model (industry, product, skill_template) is not in
here — it is versioned content on disk under `content/`, loaded by
`a2b.registry`. Only tenant-owned, mutable state lives in the database.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from .config import DB_PATH, ensure_dirs

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS vendor (
    id              TEXT PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    industry_key    TEXT NOT NULL,
    website         TEXT,
    description     TEXT,
    convey_url      TEXT,
    created_at      TEXT NOT NULL
);

-- Console operators. Distinct from customer_profile, which is the person a
-- vendor is serving; these are the people who log in and edit skills.
CREATE TABLE IF NOT EXISTS app_user (
    id              TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    name            TEXT,
    password_hash   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    last_login_at   TEXT
);

-- Only the hash of the token is stored, so a stolen database is not a set of
-- working logins.
CREATE TABLE IF NOT EXISTS auth_session (
    token_hash      TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    user_agent      TEXT
);
CREATE INDEX IF NOT EXISTS ix_auth_session_user ON auth_session(user_id);

-- Password reset. Hashed for the same reason auth_session is: the plaintext
-- exists only in the link we email, so a copy of the database cannot be used
-- to take over accounts.
--
-- `used_at` rather than deleting the row on use. A reset link that has already
-- been spent and one that was never issued should be told apart in the log even
-- though they are told the same thing, and keeping the row is what lets a
-- second click say "already used" instead of silently minting a new session.
CREATE TABLE IF NOT EXISTS password_reset (
    token_hash      TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    used_at         TEXT,
    requested_ip    TEXT
);
CREATE INDEX IF NOT EXISTS ix_password_reset_user ON password_reset(user_id);

-- Throttling, in the database rather than in memory because the API runs more
-- than one uvicorn worker and an in-process counter would give an attacker as
-- many attempts as there are workers.
--
-- One row per attempt, counted over a sliding window. Rows are pruned rather
-- than accumulated; at our volumes that is cheaper than the bookkeeping a
-- fixed-window counter needs to avoid resetting at a predictable moment.
CREATE TABLE IF NOT EXISTS auth_attempt (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket          TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_auth_attempt_bucket ON auth_attempt(bucket, created_at);

CREATE TABLE IF NOT EXISTS vendor_member (
    id              TEXT PRIMARY KEY,
    vendor_id       TEXT NOT NULL REFERENCES vendor(id) ON DELETE CASCADE,
    email           TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('admin','editor','viewer')),
    status          TEXT NOT NULL CHECK (status IN ('active','invited')),
    created_at      TEXT NOT NULL,
    UNIQUE (vendor_id, email)
);

-- A vendor's published version of a template. The pin fields are the whole
-- point: seeding copies the template at version T and freezes it. Lenni
-- shipping T+1 must never move this row.
CREATE TABLE IF NOT EXISTS vendor_skill (
    id                            TEXT PRIMARY KEY,
    vendor_id                     TEXT NOT NULL REFERENCES vendor(id) ON DELETE CASCADE,
    slug                          TEXT NOT NULL,
    name                          TEXT NOT NULL,
    product_key                   TEXT,
    seeded_from_template_id       TEXT,
    seeded_from_template_version  TEXT,
    is_custom                     INTEGER NOT NULL DEFAULT 0,
    status                        TEXT NOT NULL CHECK (status IN ('draft','published')),
    version                       INTEGER NOT NULL DEFAULT 1,
    entry_prompt                  TEXT,
    connector_only                INTEGER NOT NULL DEFAULT 0,
    entry_mode                    TEXT NOT NULL DEFAULT 'inline',
    -- Vendor overrides for the editable paragraphs of the entry prompt. Empty
    -- means "use ours", so an unedited skill keeps picking up improvements to
    -- the default wording; a key present means that paragraph is theirs and we
    -- leave it alone. The questions and the guardrail are not in here, because
    -- they are not editable from this surface.
    prompt_texts_json             TEXT NOT NULL DEFAULT '{}',
    destination_json              TEXT NOT NULL,
    created_at                    TEXT NOT NULL,
    updated_at                    TEXT NOT NULL,
    published_at                  TEXT,
    UNIQUE (vendor_id, slug)
);

CREATE TABLE IF NOT EXISTS skill_section (
    id              TEXT PRIMARY KEY,
    skill_id        TEXT NOT NULL REFERENCES vendor_skill(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    summary         TEXT,
    says            TEXT,
    asks_json       TEXT NOT NULL DEFAULT '[]',
    collects_json   TEXT NOT NULL DEFAULT '[]',
    apps_json       TEXT NOT NULL DEFAULT '[]',
    is_core         INTEGER NOT NULL DEFAULT 0,
    origin_industry TEXT,
    edited          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_section_skill ON skill_section(skill_id, position);

-- Email-keyed and deliberately not scoped to a vendor: a profile is portable
-- across vendors, which is what makes the second skill a customer runs faster
-- than the first.
CREATE TABLE IF NOT EXISTS customer_profile (
    id              TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    name            TEXT,
    facts_json      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    id                  TEXT PRIMARY KEY,
    vendor_id           TEXT REFERENCES vendor(id) ON DELETE SET NULL,
    skill_id            TEXT REFERENCES vendor_skill(id) ON DELETE SET NULL,
    skill_version       INTEGER,
    profile_id          TEXT REFERENCES customer_profile(id) ON DELETE SET NULL,
    section_status_json TEXT NOT NULL DEFAULT '{}',
    facts_json          TEXT NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL CHECK (status IN ('active','complete','abandoned')),
    terminal            TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_session_skill ON session(skill_id);

CREATE TABLE IF NOT EXISTS funnel_event (
    id          TEXT PRIMARY KEY,
    session_id  TEXT REFERENCES session(id) ON DELETE CASCADE,
    vendor_id   TEXT,
    skill_id    TEXT,
    event       TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_event_vendor ON funnel_event(vendor_id, created_at);

-- Who among us opened which company's data, and when. Kept out of funnel_event
-- because that table is the customer's journey and a bank reading their own
-- numbers should not find our support visits mixed into them.
--
-- Exists because the internal portal shows real answers — loan amounts,
-- collateral, who is borrowing. That is the right call for running support and
-- it is worth nothing to a bank asking "who at Lenni saw our applicant's file?"
-- unless somebody wrote it down.
CREATE TABLE IF NOT EXISTS staff_access (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    vendor_id   TEXT,
    session_id  TEXT,
    what        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_staff_access_at ON staff_access(created_at);

-- Ingest is suggest-then-confirm. Proposals land here unbound; nothing becomes
-- a skill until a human confirms the row.
CREATE TABLE IF NOT EXISTS ingest_run (
    id              TEXT PRIMARY KEY,
    vendor_id       TEXT REFERENCES vendor(id) ON DELETE CASCADE,
    url             TEXT NOT NULL,
    industry_key    TEXT,
    industry_confirmed INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_match (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES ingest_run(id) ON DELETE CASCADE,
    found_label     TEXT NOT NULL,
    found_quote     TEXT,
    product_key     TEXT,
    template_id     TEXT,
    confidence      TEXT NOT NULL,
    score           REAL NOT NULL DEFAULT 0,
    confirmed       INTEGER NOT NULL DEFAULT 0,
    skill_id        TEXT
);

-- What the demo workspace last published for a product, read by the /go/ links
-- at the moment of a click. Keyed by slug, not vendor id: the workspace's banks
-- live in its fixture and have no row in `vendor`.
CREATE TABLE IF NOT EXISTS live_prompt (
    vendor_slug     TEXT NOT NULL,
    product_slug    TEXT NOT NULL,
    vendor_name     TEXT NOT NULL,
    product_name    TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('published','unpublished')),
    prompt          TEXT NOT NULL DEFAULT '',
    notice          TEXT NOT NULL DEFAULT '',
    page_json       TEXT NOT NULL DEFAULT '{}',
    version         INTEGER NOT NULL DEFAULT 1,
    updated_by      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (vendor_slug, product_slug)
);

-- Every change to a live prompt, kept whole. "Who changed what our customers
-- are asked, and when?" is a question a bank will put to us, and a hash alone
-- would prove a change happened without being able to show it.
CREATE TABLE IF NOT EXISTS live_prompt_event (
    id              TEXT PRIMARY KEY,
    vendor_slug     TEXT NOT NULL,
    product_slug    TEXT NOT NULL,
    version         INTEGER NOT NULL,
    status          TEXT NOT NULL,
    prompt          TEXT NOT NULL DEFAULT '',
    prompt_sha256   TEXT NOT NULL,
    actor_email     TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_live_prompt_event
    ON live_prompt_event(vendor_slug, product_slug, version);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    finally:
        conn.close()


# Columns added after the first deployment. `CREATE TABLE IF NOT EXISTS` leaves
# an existing database alone, so a new column in SCHEMA never reaches one — and
# the deployed instance holds real vendor skills, so recreating it is not an
# option. Each entry is applied once, in order, and is a no-op afterwards.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # Legacy. Superseded by entry_mode, kept so the backfill below has something
    # to read on a database that predates it.
    ("vendor_skill", "connector_only", "INTEGER NOT NULL DEFAULT 0"),
    ("vendor_skill", "entry_mode", "TEXT NOT NULL DEFAULT 'staged'"),
    ("vendor_skill", "prompt_texts_json", "TEXT NOT NULL DEFAULT '{}'"),
)

# Run once, immediately after the named column is created — never again, or a
# vendor who deliberately moved a skill off connector-only would be dragged back
# on every restart.
_BACKFILLS: dict[tuple[str, str], str] = {
    ("vendor_skill", "entry_mode"):
        "UPDATE vendor_skill SET entry_mode='connector' WHERE connector_only=1",
}


def _migrate(conn) -> None:
    for table, column, declaration in _ADDED_COLUMNS:
        columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if columns and column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            backfill = _BACKFILLS.get((table, column))
            if backfill:
                conn.execute(backfill)


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
