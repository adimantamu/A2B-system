"""Users, passwords and sessions.

Two things worth knowing before reading further.

**Membership is keyed by email, not user id.** `vendor_member` rows are written
when someone is invited, which is usually before that person has an account. So
access is resolved by looking up the caller's email in `vendor_member`, and an
invite becomes real access the moment the invitee signs up. The alternative —
rows pointing at user ids — needs a reconciliation step every signup, and gets it
wrong when somebody is invited twice.

**Session tokens are stored hashed.** The row holds SHA-256 of the token, so a
copy of the database is not a set of working credentials. The plaintext exists
only in the caller's cookie.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import staff_emails
from .db import cursor, new_id, now

# PBKDF2 rather than argon2/bcrypt so there is no dependency to install on a box
# that may not have a compiler. The iteration count is the tuning knob; it is
# stored per-hash so it can be raised later without invalidating old passwords.
_ALGO = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SESSION_DAYS = 14

COOKIE_NAME = "a2b_session"
MIN_PASSWORD = 10
MAX_PASSWORD = 200

# A reset link is a bearer credential sitting in an inbox, so it is short-lived
# and single-use. An hour is long enough to survive a slow mail server and a
# coffee, and short enough that a forwarded or archived email stops being a way
# in.
_RESET_MINUTES = 60

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ROLES = ("admin", "editor", "viewer")
_RANK = {"viewer": 0, "editor": 1, "admin": 2}


class AuthError(Exception):
    """Something the caller can fix: bad input, wrong password, no access."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------

def hash_password(password: str, iterations: int = _ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"{_ALGO}${iterations}${b64(salt)}${b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iters)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, base64.b64decode(hash_b64))


@functools.lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A genuine hash of a value nobody holds, used to spend the same time on a
    login for an address that does not exist. Built once on first use rather
    than at import, so the cost lands on the first login instead of on every
    process start and every test collection."""
    return hash_password(secrets.token_urlsafe(32))


def _normalise_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise AuthError("That does not look like an email address.")
    return email


# Length plus a blocklist, rather than composition rules. A rule demanding a
# digit and a symbol reliably produces `Password1!`, which is on every list an
# attacker owns; length and refusing the known-common answers is both stronger
# and less irritating. Only entries at or above MIN_PASSWORD are worth listing —
# anything shorter is already refused.
_COMMON_PASSWORDS = frozenset({
    "password123", "password1234", "password12345", "passw0rd123",
    "1234567890", "12345678910", "123456789012", "qwertyuiop",
    "qwerty12345", "iloveyou123", "administrator", "letmein123",
    "welcome1234", "monkey12345", "football123", "baseball123",
    "sunshine123", "princess123", "trustno1234", "dragon12345",
    "abc123456789", "1q2w3e4r5t", "zaq12wsxcde3", "changeme123",
    "lenni123456", "a2b1234567",
})


def _check_password(password: str, email: str = "") -> None:
    password = password or ""
    if len(password) < MIN_PASSWORD:
        raise AuthError(f"Use at least {MIN_PASSWORD} characters.")
    # PBKDF2 over an unbounded input is a denial of service the caller controls:
    # the work is linear in the length and we do 600k rounds of it.
    if len(password) > MAX_PASSWORD:
        raise AuthError(f"That password is too long — {MAX_PASSWORD} characters at most.")
    if password.lower() in _COMMON_PASSWORDS:
        raise AuthError("That password is too easy to guess. Pick something less common.")
    if email:
        local = email.split("@")[0].lower()
        if password.lower() in (email.lower(), local) or (len(local) >= 4 and local in password.lower()):
            raise AuthError("Your password cannot be built out of your email address.")


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

def create_user(email: str, password: str, name: str = "") -> str:
    email = _normalise_email(email)
    _check_password(password, email)
    with cursor() as cur:
        cur.execute("SELECT id FROM app_user WHERE email = ?", (email,))
        if cur.fetchone():
            raise AuthError("An account with that email already exists.", 409)
        uid = new_id("usr")
        cur.execute(
            "INSERT INTO app_user (id, email, name, password_hash, created_at) "
            "VALUES (?,?,?,?,?)",
            (uid, email, name.strip() or email.split("@")[0], hash_password(password), now()),
        )
        # An invite written before signup becomes live access now, without any
        # reconciliation: the membership rows already carry this email.
        cur.execute(
            "UPDATE vendor_member SET status = 'active' WHERE email = ? AND status = 'invited'",
            (email,),
        )
    return uid


def get_user(user_id: str) -> Optional[dict]:
    with cursor() as cur:
        cur.execute(
            "SELECT id, email, name, created_at, last_login_at FROM app_user WHERE id = ?",
            (user_id,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def update_profile(user_id: str, name: str) -> dict:
    """Change the operator's own display name.

    Email is deliberately not editable here. Membership is keyed by email rather
    than user id — see the module docstring — so changing the address on
    `app_user` would silently strand every `vendor_member` row pointing at the
    old one, and the person would lose access to their own workspaces without
    anything reporting an error. Moving an account to a new address is an invite
    to the new address plus a removal of the old, which is a deliberate act by
    someone who already has admin on the vendor.
    """
    name = (name or "").strip()
    if not name:
        raise AuthError("Your name cannot be empty.")
    if len(name) > 120:
        raise AuthError("That name is too long — 120 characters at most.")
    with cursor() as cur:
        cur.execute("UPDATE app_user SET name = ? WHERE id = ?", (name, user_id))
        if cur.rowcount == 0:
            raise AuthError("No such account.", 404)
    return get_user(user_id)


def user_exists(email: str) -> bool:
    with cursor() as cur:
        cur.execute("SELECT 1 FROM app_user WHERE email = ?", ((email or "").strip().lower(),))
        return cur.fetchone() is not None


def authenticate(email: str, password: str) -> dict:
    email = _normalise_email(email)
    with cursor() as cur:
        cur.execute("SELECT * FROM app_user WHERE email = ?", (email,))
        row = cur.fetchone()
    # Verify against a throwaway hash when there is no such user, so the reply
    # takes the same ~600k rounds either way. Returning early here would make an
    # unknown address measurably faster to probe than a wrong password, which
    # hands back the enumeration the shared message below is protecting.
    stored = row["password_hash"] if row else _dummy_hash()
    ok = verify_password(password, stored)
    # Same message either way: distinguishing them tells an attacker which
    # addresses have accounts.
    if not row or not ok:
        raise AuthError("Email or password is incorrect.", 401)
    with cursor() as cur:
        cur.execute("UPDATE app_user SET last_login_at = ? WHERE id = ?", (now(), row["id"]))
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


def set_password(user_id: str, password: str) -> None:
    user = get_user(user_id)
    _check_password(password, user["email"] if user else "")
    with cursor() as cur:
        cur.execute(
            "UPDATE app_user SET password_hash = ? WHERE id = ?",
            (hash_password(password), user_id),
        )
        # Changing a password ends every other session — that is the point of
        # changing it.
        cur.execute("DELETE FROM auth_session WHERE user_id = ?", (user_id,))


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(user_id: str, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=_SESSION_DAYS)
    with cursor() as cur:
        cur.execute(
            "INSERT INTO auth_session (token_hash, user_id, created_at, expires_at, user_agent) "
            "VALUES (?,?,?,?,?)",
            (_digest(token), user_id, now(), expires.isoformat(timespec="seconds"), user_agent[:200]),
        )
    return token


def user_for_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    with cursor() as cur:
        cur.execute(
            "SELECT u.id, u.email, u.name, s.expires_at "
            "FROM auth_session s JOIN app_user u ON u.id = s.user_id "
            "WHERE s.token_hash = ?",
            (_digest(token),),
        )
        row = cur.fetchone()
    if not row:
        return None
    if row["expires_at"] < now():
        end_session(token)
        return None
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


def end_session(token: Optional[str]) -> None:
    if not token:
        return
    with cursor() as cur:
        cur.execute("DELETE FROM auth_session WHERE token_hash = ?", (_digest(token),))


def session_count(user_id: str) -> int:
    """Live sessions for this user. Expired rows are not counted."""
    with cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM auth_session WHERE user_id = ? AND expires_at >= ?",
            (user_id, now()),
        )
        return int(cur.fetchone()["n"])


def end_other_sessions(user_id: str, keep_token: Optional[str]) -> int:
    """Sign out everywhere except the caller's own browser.

    Changing a password already does this wholesale. This exists for the case
    that is not a password change: a shared machine, or a laptop left signed in
    somewhere, where the person wants the other sessions gone but has no reason
    to pick a new password and be signed out of the tab they are looking at.
    """
    with cursor() as cur:
        if keep_token:
            cur.execute(
                "DELETE FROM auth_session WHERE user_id = ? AND token_hash != ?",
                (user_id, _digest(keep_token)),
            )
        else:
            cur.execute("DELETE FROM auth_session WHERE user_id = ?", (user_id,))
        return cur.rowcount


def purge_expired_sessions() -> int:
    with cursor() as cur:
        cur.execute("DELETE FROM auth_session WHERE expires_at < ?", (now(),))
        return cur.rowcount


# --------------------------------------------------------------------------
# Throttling
# --------------------------------------------------------------------------

# (attempts, window seconds) per bucket kind.
#
# Two axes on purpose. Per-address stops someone grinding one account; per-IP
# stops them spreading the same number of guesses across many addresses, which
# a per-address limit alone does not see. The address limits are the tighter of
# the two because a shared office NAT can legitimately produce a lot of traffic
# from one IP and we do not want to lock out a bank.
#
# Note what is *not* here: a lockout flag on app_user. A persistent lock keyed
# to the account is a denial of service an attacker can trigger on purpose by
# guessing at someone. A sliding window expires on its own.
_LIMITS: dict[str, tuple[int, int]] = {
    "login:email":  (10, 15 * 60),
    "login:ip":     (50, 15 * 60),
    "forgot:email": (5, 60 * 60),
    "forgot:ip":    (20, 60 * 60),
    "signup:ip":    (10, 60 * 60),
}

_TOO_MANY = "Too many attempts. Wait a few minutes and try again."


def _bucket(kind: str, key: str) -> str:
    """Identify a throttle bucket without storing what it is.

    The key is an email address or an IP, and the counter only ever needs to
    ask whether two keys are equal — never to read one back. So it is hashed,
    and the table holds no addresses.

    That matters because throttling has to happen before we know whether an
    account exists: `forgot` records an attempt for every address submitted,
    so a plaintext column would let any caller write arbitrary personal data
    into our database about people who are not even users.

    Abuse forensics is a separate need and should be a deliberate log with a
    retention rule, not a side effect of rate limiting.
    """
    return f"{kind}|{hashlib.sha256((key or '').strip().lower().encode()).hexdigest()}"


def _window_start(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def record_attempt(kind: str, key: str) -> None:
    if kind not in _LIMITS:
        raise KeyError(f"unknown throttle bucket: {kind}")
    with cursor() as cur:
        cur.execute(
            "INSERT INTO auth_attempt (bucket, created_at) VALUES (?,?)",
            (_bucket(kind, key), now()),
        )


def check_rate(kind: str, key: str) -> None:
    """Raise before doing the work if this bucket is over its limit.

    The message is deliberately the same whatever the bucket, and says nothing
    about whether the address exists — a throttle that only triggers for real
    accounts would be an enumeration oracle wearing a hard hat.
    """
    allowed, window = _LIMITS[kind]
    with cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM auth_attempt WHERE bucket = ? AND created_at >= ?",
            (_bucket(kind, key), _window_start(window)),
        )
        if int(cur.fetchone()["n"]) >= allowed:
            raise AuthError(_TOO_MANY, 429)


def clear_attempts(kind: str, key: str) -> None:
    """Forget this bucket. Called when a login succeeds, so a person who
    fumbled their password four times and then got it right is not carrying
    four strikes into tomorrow."""
    with cursor() as cur:
        cur.execute("DELETE FROM auth_attempt WHERE bucket = ?", (_bucket(kind, key),))


def purge_old_attempts() -> int:
    """Drop rows older than the longest window. Nothing reads them after that
    and the table is append-heavy."""
    longest = max(window for _, window in _LIMITS.values())
    with cursor() as cur:
        cur.execute("DELETE FROM auth_attempt WHERE created_at < ?", (_window_start(longest),))
        return cur.rowcount


# --------------------------------------------------------------------------
# Password reset
# --------------------------------------------------------------------------

def begin_password_reset(email: str, ip: str = "") -> Optional[tuple[dict, str]]:
    """Issue a reset token, or return None when there is no account.

    The caller must answer identically either way. Returning None rather than
    raising is what makes that easy to get right at the route.

    Any unused token already outstanding for this user is burned first. Two live
    links would mean an older email — possibly the one an attacker triggered —
    still works after the owner has used the newer one.
    """
    email = (email or "").strip().lower()
    with cursor() as cur:
        cur.execute("SELECT id, email, name FROM app_user WHERE email = ?", (email,))
        row = cur.fetchone()
    if not row:
        return None

    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=_RESET_MINUTES)
    with cursor() as cur:
        cur.execute(
            "UPDATE password_reset SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
            (now(), row["id"]),
        )
        cur.execute(
            "INSERT INTO password_reset (token_hash, user_id, created_at, expires_at, requested_ip) "
            "VALUES (?,?,?,?,?)",
            (_digest(token), row["id"], now(),
             expires.isoformat(timespec="seconds"), (ip or "")[:45]),
        )
    return dict(row), token


def reset_token_valid(token: str) -> bool:
    """Whether this token would work, without spending it.

    The reset page calls this on load. Consuming on GET would mean a mail
    client that prefetches links burns the token before the person clicks it,
    and they would arrive at an error for a link that had never been used.
    """
    if not token:
        return False
    with cursor() as cur:
        cur.execute(
            "SELECT expires_at, used_at FROM password_reset WHERE token_hash = ?",
            (_digest(token),),
        )
        row = cur.fetchone()
    return bool(row) and row["used_at"] is None and row["expires_at"] >= now()


def complete_password_reset(token: str, new_password: str) -> dict:
    """Spend the token and set the password. Returns the user.

    The UPDATE carries `used_at IS NULL` in its WHERE clause and the rowcount is
    checked, so two clicks racing each other cannot both win — SQLite serialises
    the writes and the loser sees zero rows changed.

    The password is checked before the token is spent. Getting that order wrong
    means someone whose first choice was too short has burned their only link
    and has to go back to their email for another, which reads as the reset
    being broken.
    """
    if not token:
        raise AuthError("That reset link is invalid or has expired. Request a new one.")
    digest = _digest(token)
    stale = "That reset link is invalid or has expired. Request a new one."

    with cursor() as cur:
        cur.execute(
            "SELECT user_id FROM password_reset "
            "WHERE token_hash = ? AND used_at IS NULL AND expires_at >= ?",
            (digest, now()),
        )
        row = cur.fetchone()
    if not row:
        raise AuthError(stale)

    user = get_user(row["user_id"])
    if not user:
        raise AuthError(stale)
    # Raises on a weak password, before anything has been written.
    _check_password(new_password, user["email"])

    with cursor() as cur:
        cur.execute(
            "UPDATE password_reset SET used_at = ? "
            "WHERE token_hash = ? AND used_at IS NULL AND expires_at >= ?",
            (now(), digest, now()),
        )
        if cur.rowcount == 0:
            raise AuthError(stale)
    user_id = user["id"]
    # set_password drops every session for this user, which is the point: the
    # reason someone resets a password is that they suspect somebody else has
    # it, and that somebody may be signed in right now.
    set_password(user_id, new_password)
    return user


def invited_without_account(email: str) -> bool:
    """Invited to a workspace but never signed up.

    Membership is keyed by email and written at invite time, so this state is
    normal rather than exceptional, and a forgot-password from one of these
    people would otherwise be answered with silence they cannot act on.
    """
    with cursor() as cur:
        cur.execute(
            "SELECT 1 FROM vendor_member WHERE email = ? LIMIT 1",
            ((email or "").strip().lower(),),
        )
        return cur.fetchone() is not None


def purge_expired_resets() -> int:
    with cursor() as cur:
        cur.execute("DELETE FROM password_reset WHERE expires_at < ?", (now(),))
        return cur.rowcount


# --------------------------------------------------------------------------
# Authorisation
# --------------------------------------------------------------------------

def is_staff(email: str) -> bool:
    """Whether this address is ours.

    Separate from `vendor_member` on purpose. Every other permission in the
    system answers "what may this person do to this one company"; this one
    answers "is this person us", and folding it into the membership table would
    have made staff look like a role a vendor admin could hand out.
    """
    return (email or "").strip().lower() in staff_emails()


def role_for(email: str, vendor_id: str) -> Optional[str]:
    """The caller's role on a vendor, or None if they have no access at all."""
    with cursor() as cur:
        cur.execute(
            "SELECT role FROM vendor_member WHERE vendor_id = ? AND email = ? AND status = 'active'",
            (vendor_id, (email or "").lower()),
        )
        row = cur.fetchone()
    return row["role"] if row else None


def require_role(email: str, vendor_id: str, minimum: str = "viewer") -> str:
    role = role_for(email, vendor_id)
    # 404 rather than 403 for non-members: confirming that a vendor id exists is
    # itself information the caller has not earned.
    if role is None:
        raise AuthError("No such vendor.", 404)
    if _RANK[role] < _RANK[minimum]:
        raise AuthError(f"This action needs the {minimum} role; you have {role}.", 403)
    return role


def vendors_for(email: str) -> list[dict]:
    with cursor() as cur:
        cur.execute(
            "SELECT v.*, m.role FROM vendor v "
            "JOIN vendor_member m ON m.vendor_id = v.id "
            "WHERE m.email = ? AND m.status = 'active' "
            "ORDER BY v.created_at",
            ((email or "").lower(),),
        )
        return [dict(r) for r in cur.fetchall()]


def vendor_for_skill(skill_id: str) -> Optional[str]:
    with cursor() as cur:
        cur.execute("SELECT vendor_id FROM vendor_skill WHERE id = ?", (skill_id,))
        row = cur.fetchone()
    return row["vendor_id"] if row else None


def vendor_for_slug(slug: str) -> Optional[str]:
    with cursor() as cur:
        cur.execute("SELECT id FROM vendor WHERE slug = ?", (slug,))
        row = cur.fetchone()
    return row["id"] if row else None


def vendor_for_section(section_id: str) -> Optional[str]:
    with cursor() as cur:
        cur.execute(
            "SELECT s.vendor_id FROM skill_section sec "
            "JOIN vendor_skill s ON s.id = sec.skill_id WHERE sec.id = ?",
            (section_id,),
        )
        row = cur.fetchone()
    return row["vendor_id"] if row else None


def cookie_is_secure() -> bool:
    """Whether to set the Secure flag. Off by default because the first deploy
    is plain HTTP and a Secure cookie would simply never be sent; turn it on in
    the environment the moment TLS is in front of this."""
    return os.environ.get("A2B_COOKIE_SECURE", "").lower() in ("1", "true", "yes")
