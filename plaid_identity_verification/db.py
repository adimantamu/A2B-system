"""SQLite persistence with organization-level tenancy."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from config import settings
from security import short_token, token, token_hash


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.database_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode = WAL;

            CREATE TABLE IF NOT EXISTS organization (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                join_token_hash TEXT NOT NULL UNIQUE,
                enrollment_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS member (
                id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL REFERENCES organization(id),
                name TEXT NOT NULL,
                email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'employee')),
                link_slug TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_session (
                token_hash TEXT PRIMARY KEY,
                member_id TEXT NOT NULL REFERENCES member(id) ON DELETE CASCADE,
                csrf_token TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS verification (
                id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL REFERENCES organization(id),
                member_id TEXT NOT NULL REFERENCES member(id),
                client_user_id TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL,
                external_id TEXT UNIQUE,
                status TEXT NOT NULL,
                subject_name TEXT NOT NULL,
                subject_email TEXT,
                consent_version TEXT NOT NULL,
                consent_at TEXT NOT NULL,
                request_ip TEXT,
                completion_token_hash TEXT NOT NULL,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                employee_notified_at TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_event (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id TEXT,
                member_id TEXT,
                action TEXT NOT NULL,
                target_id TEXT,
                request_ip TEXT,
                detail_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_member_org
                ON member(organization_id);
            CREATE INDEX IF NOT EXISTS idx_verification_member
                ON verification(member_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_verification_org
                ON verification(organization_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_verification_external
                ON verification(external_id);
            """
        )


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def audit(
    action: str,
    *,
    organization_id: str | None = None,
    member_id: str | None = None,
    target_id: str | None = None,
    request_ip: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_event (
                organization_id, member_id, action, target_id,
                request_ip, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                organization_id,
                member_id,
                action,
                target_id,
                request_ip,
                json.dumps(detail or {}, separators=(",", ":")),
                now_iso(),
            ),
        )


def create_organization(
    *,
    organization_name: str,
    member_name: str,
    email: str,
    encoded_password: str,
    request_ip: str | None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    organization_id = new_id("org")
    member_id = new_id("mem")
    join_value = token(18)
    link_slug = short_token()
    created_at = now_iso()

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            INSERT INTO organization (
                id, name, join_token_hash, enrollment_enabled, created_at
            ) VALUES (?, ?, ?, 1, ?)
            """,
            (
                organization_id,
                organization_name,
                token_hash(join_value),
                created_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO member (
                id, organization_id, name, email, password_hash,
                role, link_slug, active, created_at
            ) VALUES (?, ?, ?, ?, ?, 'admin', ?, 1, ?)
            """,
            (
                member_id,
                organization_id,
                member_name,
                email,
                encoded_password,
                link_slug,
                created_at,
            ),
        )
    organization = get_organization(organization_id)
    member = get_member(member_id)
    assert organization and member
    audit(
        "organization.created",
        organization_id=organization_id,
        member_id=member_id,
        request_ip=request_ip,
    )
    return organization, member, join_value


def get_organization(organization_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        return _row(
            conn.execute(
                "SELECT * FROM organization WHERE id = ?",
                (organization_id,),
            ).fetchone()
        )


def get_member(member_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        return _row(
            conn.execute(
                """
                SELECT m.*, o.name AS organization_name,
                       o.enrollment_enabled
                FROM member m
                JOIN organization o ON o.id = m.organization_id
                WHERE m.id = ?
                """,
                (member_id,),
            ).fetchone()
        )


def get_member_by_email(email: str) -> dict[str, Any] | None:
    with connect() as conn:
        return _row(
            conn.execute(
                """
                SELECT m.*, o.name AS organization_name,
                       o.enrollment_enabled
                FROM member m
                JOIN organization o ON o.id = m.organization_id
                WHERE m.email = ? COLLATE NOCASE
                """,
                (email,),
            ).fetchone()
        )


def get_member_by_link(link_slug: str) -> dict[str, Any] | None:
    with connect() as conn:
        return _row(
            conn.execute(
                """
                SELECT m.*, o.name AS organization_name,
                       o.enrollment_enabled
                FROM member m
                JOIN organization o ON o.id = m.organization_id
                WHERE m.link_slug = ? AND m.active = 1
                """,
                (link_slug,),
            ).fetchone()
        )


def create_employee(
    *,
    invite_token: str,
    member_name: str,
    email: str,
    encoded_password: str,
    request_ip: str | None,
) -> dict[str, Any]:
    invite_hash = token_hash(invite_token)
    with connect() as conn:
        organization = conn.execute(
            """
            SELECT * FROM organization
            WHERE join_token_hash = ? AND enrollment_enabled = 1
            """,
            (invite_hash,),
        ).fetchone()
        if not organization:
            raise PermissionError("That enrollment link is invalid or closed.")
        member_id = new_id("mem")
        conn.execute(
            """
            INSERT INTO member (
                id, organization_id, name, email, password_hash,
                role, link_slug, active, created_at
            ) VALUES (?, ?, ?, ?, ?, 'employee', ?, 1, ?)
            """,
            (
                member_id,
                organization["id"],
                member_name,
                email,
                encoded_password,
                short_token(),
                now_iso(),
            ),
        )
    member = get_member(member_id)
    assert member
    audit(
        "employee.created",
        organization_id=member["organization_id"],
        member_id=member_id,
        request_ip=request_ip,
    )
    return member


def list_members(organization_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, name, email, role, link_slug, active, created_at
                FROM member
                WHERE organization_id = ?
                ORDER BY role, name COLLATE NOCASE
                """,
                (organization_id,),
            ).fetchall()
        ]


def rotate_join_token(organization_id: str) -> str:
    join_value = token(18)
    with connect() as conn:
        conn.execute(
            """
            UPDATE organization
            SET join_token_hash = ?, enrollment_enabled = 1
            WHERE id = ?
            """,
            (token_hash(join_value), organization_id),
        )
    return join_value


def set_enrollment(organization_id: str, enabled: bool) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE organization SET enrollment_enabled = ? WHERE id = ?",
            (1 if enabled else 0, organization_id),
        )


def create_auth_session(
    member_id: str,
    *,
    ttl_seconds: int,
) -> tuple[str, str]:
    raw_token = token()
    csrf = token(18)
    expires_at = int(time.time()) + ttl_seconds
    with connect() as conn:
        conn.execute(
            "DELETE FROM auth_session WHERE expires_at < ?",
            (int(time.time()),),
        )
        conn.execute(
            """
            INSERT INTO auth_session (
                token_hash, member_id, csrf_token, expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                token_hash(raw_token),
                member_id,
                csrf,
                expires_at,
                now_iso(),
            ),
        )
    return raw_token, csrf


def member_for_session(raw_token: str | None) -> dict[str, Any] | None:
    if not raw_token:
        return None
    with connect() as conn:
        row = conn.execute(
            """
            SELECT m.*, o.name AS organization_name,
                   o.enrollment_enabled, s.csrf_token, s.expires_at
            FROM auth_session s
            JOIN member m ON m.id = s.member_id
            JOIN organization o ON o.id = m.organization_id
            WHERE s.token_hash = ? AND s.expires_at >= ? AND m.active = 1
            """,
            (token_hash(raw_token), int(time.time())),
        ).fetchone()
    return _row(row)


def delete_auth_session(raw_token: str | None) -> None:
    if not raw_token:
        return
    with connect() as conn:
        conn.execute(
            "DELETE FROM auth_session WHERE token_hash = ?",
            (token_hash(raw_token),),
        )


def create_verification(
    *,
    member: dict[str, Any],
    provider: str,
    subject_name: str,
    subject_email: str | None,
    consent_version: str,
    request_ip: str | None,
) -> tuple[dict[str, Any], str]:
    verification_id = new_id("ver")
    completion_token = token(24)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO verification (
                id, organization_id, member_id, client_user_id,
                provider, status, subject_name, subject_email,
                consent_version, consent_at, request_ip,
                completion_token_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                verification_id,
                member["organization_id"],
                member["id"],
                f"piv_{uuid.uuid4().hex}",
                provider,
                subject_name,
                subject_email,
                consent_version,
                now_iso(),
                request_ip,
                token_hash(completion_token),
                now_iso(),
            ),
        )
    verification = get_verification(verification_id)
    assert verification
    audit(
        "verification.started",
        organization_id=member["organization_id"],
        member_id=member["id"],
        target_id=verification_id,
        request_ip=request_ip,
        detail={"provider": provider},
    )
    return verification, completion_token


def set_external_verification(
    verification_id: str,
    *,
    external_id: str,
    status: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE verification
            SET external_id = ?, status = ?, error = NULL
            WHERE id = ?
            """,
            (external_id, status, verification_id),
        )


def set_verification_error(verification_id: str, message: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE verification SET status = 'error', error = ? WHERE id = ?",
            (message[:1000], verification_id),
        )


def get_verification(verification_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT v.*, m.name AS employee_name, m.email AS employee_email,
                   m.link_slug, o.name AS organization_name
            FROM verification v
            JOIN member m ON m.id = v.member_id
            JOIN organization o ON o.id = v.organization_id
            WHERE v.id = ?
            """,
            (verification_id,),
        ).fetchone()
    result = _row(row)
    if result:
        result["result"] = json.loads(result.pop("result_json") or "{}")
    return result


def get_verification_by_external(external_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM verification WHERE external_id = ?",
            (external_id,),
        ).fetchone()
    return get_verification(row["id"]) if row else None


def completion_token_valid(verification_id: str, raw_token: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM verification
            WHERE id = ? AND completion_token_hash = ?
            """,
            (verification_id, token_hash(raw_token)),
        ).fetchone()
    return bool(row)


def complete_verification(
    verification_id: str,
    *,
    status: str,
    result: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    completed_at = now_iso() if status in {
        "success",
        "failed",
        "pending_review",
        "canceled",
        "expired",
    } else None
    with connect() as conn:
        conn.execute(
            """
            UPDATE verification
            SET status = ?, result_json = ?, error = ?,
                completed_at = COALESCE(?, completed_at)
            WHERE id = ?
            """,
            (
                status,
                json.dumps(result, separators=(",", ":")),
                error,
                completed_at,
                verification_id,
            ),
        )
    verification = get_verification(verification_id)
    assert verification
    return verification


def mark_notified(verification_id: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE verification
            SET employee_notified_at = COALESCE(employee_notified_at, ?)
            WHERE id = ?
            """,
            (now_iso(), verification_id),
        )


def list_verifications(member: dict[str, Any]) -> list[dict[str, Any]]:
    scope = "v.organization_id = ?" if member["role"] == "admin" else "v.member_id = ?"
    value = (
        member["organization_id"]
        if member["role"] == "admin"
        else member["id"]
    )
    with connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT v.id, v.status, v.subject_name, v.subject_email,
                       v.provider, v.created_at, v.completed_at,
                       m.name AS employee_name
                FROM verification v
                JOIN member m ON m.id = v.member_id
                WHERE {scope}
                ORDER BY v.created_at DESC
                LIMIT 100
                """,
                (value,),
            ).fetchall()
        ]


def recent_link_attempts(member_id: str, since_iso: str) -> int:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM verification
            WHERE member_id = ? AND created_at >= ?
            """,
            (member_id, since_iso),
        ).fetchone()
    return int(row["n"])
