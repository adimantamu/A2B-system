"""Password reset, throttling, and the sign-in pages.

The theme is the awkward half of authentication. Signing in with the right
password is easy; what decides whether this is safe is what happens on the
paths nobody demos — a link clicked twice, a link clicked an hour late, a
stranger probing which addresses have accounts, and somebody working through
the password list.
"""
from __future__ import annotations

import re
import sys

import pytest
from fastapi.testclient import TestClient

from a2b import auth, mailer
from a2b.api.main import app
from a2b.config import DATA_DIR
from a2b.db import cursor, now


@pytest.fixture
def client():
    return TestClient(app)


PW = "correct-horse-staple"


def signup(c, email, password=PW, name="Sarah Whitley"):
    r = c.post("/api/auth/signup", json={"email": email, "password": password, "name": name})
    r.raise_for_status()
    return r


def outbox():
    """Every message the outbox backend has written, newest last."""
    d = DATA_DIR / "outbox"
    if not d.exists():
        return []
    return [p.read_text(encoding="utf-8") for p in sorted(d.iterdir())]


def link_token(body: str) -> str:
    m = re.search(r"reset-password\?token=([\w\-]+)", body)
    assert m, f"no reset link in:\n{body}"
    return m.group(1)


def request_reset(c, email) -> str:
    """Ask for a reset and return the token that was actually emailed."""
    before = len(outbox())
    r = c.post("/api/auth/forgot", json={"email": email})
    r.raise_for_status()
    sent = outbox()
    assert len(sent) > before, "no email was written"
    return link_token(sent[-1])


# --------------------------------------------------------------------------
# The happy path, end to end through the email
# --------------------------------------------------------------------------

def test_reset_link_sets_a_new_password_and_signs_in(client):
    signup(client, "sarah@bluebonnetbank.com")
    token = request_reset(client, "sarah@bluebonnetbank.com")

    fresh = TestClient(app)
    r = fresh.post("/api/auth/reset", json={"token": token, "password": "new-lake-travis-77"})
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "sarah@bluebonnetbank.com"
    assert fresh.cookies.get(auth.COOKIE_NAME), "reset should leave them signed in"

    # The old password is gone and the new one works.
    other = TestClient(app)
    assert other.post("/api/auth/login",
                      json={"email": "sarah@bluebonnetbank.com", "password": PW}).status_code == 401
    assert other.post("/api/auth/login",
                      json={"email": "sarah@bluebonnetbank.com",
                            "password": "new-lake-travis-77"}).status_code == 200


def test_reset_email_is_addressed_to_the_account_and_carries_one_link(client):
    signup(client, "sarah@bluebonnetbank.com")
    request_reset(client, "sarah@bluebonnetbank.com")
    body = outbox()[-1]
    assert "To: sarah@bluebonnetbank.com" in body
    assert len(re.findall(r"reset-password\?token=", body)) == 1


def test_reset_revokes_other_sessions(client):
    """Reset is what you reach for when you think someone else is in your
    account, so it has to end their session, not just change the password."""
    signup(client, "sarah@bluebonnetbank.com")
    attacker = TestClient(app)
    attacker.post("/api/auth/login",
                  json={"email": "sarah@bluebonnetbank.com", "password": PW}).raise_for_status()
    assert attacker.get("/api/auth/me").json()["user"] is not None

    token = request_reset(client, "sarah@bluebonnetbank.com")
    TestClient(app).post("/api/auth/reset",
                         json={"token": token, "password": "new-lake-travis-77"}).raise_for_status()

    assert attacker.get("/api/auth/me").json()["user"] is None


# --------------------------------------------------------------------------
# Tokens: spent, stale, superseded, forged
# --------------------------------------------------------------------------

def test_token_works_exactly_once(client):
    signup(client, "sarah@bluebonnetbank.com")
    token = request_reset(client, "sarah@bluebonnetbank.com")
    first = TestClient(app).post("/api/auth/reset",
                                 json={"token": token, "password": "new-lake-travis-77"})
    assert first.status_code == 200
    second = TestClient(app).post("/api/auth/reset",
                                  json={"token": token, "password": "different-one-99"})
    assert second.status_code == 400


def test_requesting_again_invalidates_the_earlier_link(client):
    """Two links in an inbox is a trap: people click the older one. Only the
    newest may work."""
    signup(client, "sarah@bluebonnetbank.com")
    first = request_reset(client, "sarah@bluebonnetbank.com")
    second = request_reset(client, "sarah@bluebonnetbank.com")
    assert first != second

    assert TestClient(app).post("/api/auth/reset",
                                json={"token": first, "password": "new-lake-travis-77"}
                                ).status_code == 400
    assert TestClient(app).post("/api/auth/reset",
                                json={"token": second, "password": "new-lake-travis-77"}
                                ).status_code == 200


def test_expired_token_is_refused(client):
    signup(client, "sarah@bluebonnetbank.com")
    token = request_reset(client, "sarah@bluebonnetbank.com")
    with cursor() as cur:
        cur.execute("UPDATE password_reset SET expires_at=?", ("2020-01-01T00:00:00+00:00",))
    assert TestClient(app).post("/api/auth/reset",
                                json={"token": token, "password": "new-lake-travis-77"}
                                ).status_code == 400


@pytest.mark.parametrize("token", ["", "not-a-token", "x" * 64])
def test_forged_tokens_are_refused(client, token):
    assert TestClient(app).post("/api/auth/reset",
                                json={"token": token, "password": "new-lake-travis-77"}
                                ).status_code == 400


def test_plaintext_token_is_not_stored(client):
    """A stolen copy of the database must not be usable to take over accounts."""
    signup(client, "sarah@bluebonnetbank.com")
    token = request_reset(client, "sarah@bluebonnetbank.com")
    with cursor() as cur:
        rows = cur.execute("SELECT token_hash FROM password_reset").fetchall()
    assert rows
    assert all(r["token_hash"] != token for r in rows)


def test_checking_a_token_does_not_spend_it(client):
    """Mail clients and link scanners fetch URLs before a human sees them. If
    rendering the page consumed the token, the person would arrive at a dead
    link they never clicked."""
    signup(client, "sarah@bluebonnetbank.com")
    token = request_reset(client, "sarah@bluebonnetbank.com")
    for _ in range(3):
        assert client.get(f"/api/auth/reset?token={token}").json()["valid"] is True
        assert client.get(f"/reset-password?token={token}").status_code == 200
    assert TestClient(app).post("/api/auth/reset",
                                json={"token": token, "password": "new-lake-travis-77"}
                                ).status_code == 200


def test_reset_enforces_password_rules(client):
    signup(client, "sarah@bluebonnetbank.com")
    token = request_reset(client, "sarah@bluebonnetbank.com")
    weak = TestClient(app).post("/api/auth/reset", json={"token": token, "password": "short"})
    assert weak.status_code == 400
    # Rejecting the password must not burn the token — they get to try again.
    assert TestClient(app).post("/api/auth/reset",
                                json={"token": token, "password": "new-lake-travis-77"}
                                ).status_code == 200


# --------------------------------------------------------------------------
# Not leaking who has an account
# --------------------------------------------------------------------------

def test_forgot_answers_identically_for_unknown_addresses(client):
    signup(client, "sarah@bluebonnetbank.com")
    real = client.post("/api/auth/forgot", json={"email": "sarah@bluebonnetbank.com"})
    fake = client.post("/api/auth/forgot", json={"email": "nobody@example.com"})
    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json()


def test_no_email_is_sent_to_an_address_without_an_account(client):
    before = len(outbox())
    client.post("/api/auth/forgot", json={"email": "nobody@example.com"}).raise_for_status()
    assert len(outbox()) == before


def test_login_does_not_distinguish_unknown_email_from_wrong_password(client):
    signup(client, "sarah@bluebonnetbank.com")
    wrong_pw = client.post("/api/auth/login",
                           json={"email": "sarah@bluebonnetbank.com", "password": "wrong-one-here"})
    no_user = client.post("/api/auth/login",
                          json={"email": "ghost@example.com", "password": "wrong-one-here"})
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["detail"] == no_user.json()["detail"]


def test_invited_address_without_an_account_is_pointed_at_signup(client):
    """An invitee who tries to reset has no account yet — the invitation is
    keyed to their email. Saying nothing would strand them, so they get mail
    that sends them to signup instead of a reset link."""
    owner = TestClient(app)
    signup(owner, "owner@bluebonnetbank.com")
    vid = owner.post("/api/vendors", json={"name": "Bluebonnet", "industry_key": "banking"}
                     ).json()["vendor"]["id"]
    owner.post(f"/api/vendors/{vid}/members",
               json={"email": "newhire@bluebonnetbank.com", "role": "editor"}).raise_for_status()

    before = len(outbox())
    client.post("/api/auth/forgot", json={"email": "newhire@bluebonnetbank.com"}).raise_for_status()
    sent = outbox()
    assert len(sent) > before
    assert "signup" in sent[-1]
    assert "reset-password?token=" not in sent[-1]


def test_case_and_whitespace_do_not_create_a_second_identity(client):
    signup(client, "sarah@bluebonnetbank.com")
    token = request_reset(client, "  Sarah@BluebonnetBank.COM  ")
    assert TestClient(app).post("/api/auth/reset",
                                json={"token": token, "password": "new-lake-travis-77"}
                                ).status_code == 200


# --------------------------------------------------------------------------
# Throttling
# --------------------------------------------------------------------------

def test_login_throttles_after_repeated_failures(client):
    signup(client, "sarah@bluebonnetbank.com")
    codes = [client.post("/api/auth/login",
                         json={"email": "sarah@bluebonnetbank.com", "password": "wrong-one-here"}
                         ).status_code for _ in range(14)]
    assert 429 in codes, "brute force was never slowed down"
    assert codes.index(429) >= 5, "a few typos must not lock someone out"


def test_throttle_does_not_leak_whether_the_account_exists(client):
    """A 429 that only ever appears for real accounts is an oracle. The limit
    is keyed on the submitted address, whether or not it resolves."""
    codes = [client.post("/api/auth/login",
                         json={"email": "ghost@example.com", "password": "wrong-one-here"}
                         ).status_code for _ in range(14)]
    assert 429 in codes


def test_throttle_survives_a_correct_password(client):
    """Otherwise the limit is no limit: an attacker who guesses right on
    attempt 900 was never stopped, and one who is throttled could clear it."""
    signup(client, "sarah@bluebonnetbank.com")
    for _ in range(14):
        client.post("/api/auth/login",
                    json={"email": "sarah@bluebonnetbank.com", "password": "wrong-one-here"})
    blocked = client.post("/api/auth/login",
                          json={"email": "sarah@bluebonnetbank.com", "password": PW})
    assert blocked.status_code == 429


def test_signing_in_clears_the_failure_count(client):
    """Someone who mistypes twice and then gets it right should not be carrying
    a grudge into next week."""
    signup(client, "sarah@bluebonnetbank.com")
    for _ in range(3):
        client.post("/api/auth/login",
                    json={"email": "sarah@bluebonnetbank.com", "password": "wrong-one-here"})
    client.post("/api/auth/login",
                json={"email": "sarah@bluebonnetbank.com", "password": PW}).raise_for_status()
    codes = [client.post("/api/auth/login",
                         json={"email": "sarah@bluebonnetbank.com", "password": "wrong-one-here"}
                         ).status_code for _ in range(8)]
    assert 429 not in codes


def test_forgot_is_throttled(client):
    """Unthrottled, this endpoint is a free email cannon aimed at our users."""
    signup(client, "sarah@bluebonnetbank.com")
    codes = [client.post("/api/auth/forgot", json={"email": "sarah@bluebonnetbank.com"}
                         ).status_code for _ in range(9)]
    assert 429 in codes


def test_throttle_stores_no_addresses(client):
    """Throttling has to record an attempt before we know whether the account
    exists, so a plaintext column would let any stranger write personal data
    about third parties into our database. It only needs equality."""
    client.post("/api/auth/forgot", json={"email": "nobody@example.com"})
    client.post("/api/auth/login", json={"email": "ghost@example.com", "password": "nope-nope-1"})
    with cursor() as cur:
        buckets = " ".join(r["bucket"] for r in cur.execute("SELECT bucket FROM auth_attempt"))
    assert buckets, "nothing was recorded"
    assert "nobody@example.com" not in buckets
    assert "ghost@example.com" not in buckets
    assert "@" not in buckets


def test_purges_drop_spent_and_stale_rows(client):
    signup(client, "sarah@bluebonnetbank.com")
    request_reset(client, "sarah@bluebonnetbank.com")
    client.post("/api/auth/login", json={"email": "sarah@bluebonnetbank.com", "password": "x" * 12})
    with cursor() as cur:
        cur.execute("UPDATE password_reset SET expires_at=?", ("2020-01-01T00:00:00+00:00",))
        cur.execute("UPDATE auth_attempt SET created_at=?", ("2020-01-01T00:00:00+00:00",))
    assert auth.purge_expired_resets() >= 1
    assert auth.purge_old_attempts() >= 1
    with cursor() as cur:
        assert cur.execute("SELECT COUNT(*) c FROM password_reset").fetchone()["c"] == 0
        assert cur.execute("SELECT COUNT(*) c FROM auth_attempt").fetchone()["c"] == 0


# --------------------------------------------------------------------------
# Password rules
# --------------------------------------------------------------------------

@pytest.mark.parametrize("password", [
    "short",            # under the floor
    "password123",      # on every list ever leaked
    "1234567890",
    "sarah@bluebonnetbank.com",   # the address itself
    "sarah",                       # the local part
    "sarah-is-here",               # the local part buried in it
])
def test_weak_passwords_are_refused_at_signup(client, password):
    r = client.post("/api/auth/signup",
                    json={"email": "sarah@bluebonnetbank.com", "password": password, "name": "S"})
    assert r.status_code == 400, f"accepted {password!r}"


def test_absurdly_long_password_is_refused(client):
    """Every candidate costs 600,000 PBKDF2 rounds over whatever it is handed,
    so an unbounded field is a way to make the server do the attacker's work."""
    r = client.post("/api/auth/signup",
                    json={"email": "sarah@bluebonnetbank.com", "password": "a" * 5000, "name": "S"})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# The pages
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path,needle", [
    ("/login", "current-password"),
    ("/signup", "new-password"),
    ("/forgot-password", "/api/auth/forgot"),
])
def test_pages_render_a_usable_form(client, path, needle):
    r = client.get(path)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<form" in r.text
    assert 'autocomplete="username"' in r.text
    assert needle in r.text


def test_pages_are_not_indexable(client):
    """A sign-in page in search results is a phishing aid and tells crawlers
    where to point credential stuffing."""
    for path in ("/login", "/signup", "/forgot-password", "/reset-password"):
        assert 'name="robots" content="noindex,nofollow"' in client.get(path).text


def test_reset_page_suppresses_the_referrer(client):
    """The token is in the query string, so any outbound request from this page
    would otherwise carry a live credential in the Referer header."""
    assert 'name="referrer" content="no-referrer"' in client.get("/reset-password?token=x").text


def test_reset_page_says_so_when_the_link_is_dead(client):
    r = client.get("/reset-password?token=stale")
    assert "expired" in r.text.lower()
    assert "<form" not in r.text, "no point offering a form that cannot work"


def test_reset_page_offers_a_form_for_a_live_link(client):
    signup(client, "sarah@bluebonnetbank.com")
    token = request_reset(client, "sarah@bluebonnetbank.com")
    r = client.get(f"/reset-password?token={token}")
    assert "<form" in r.text
    assert token in r.text


@pytest.mark.parametrize("hostile", [
    "//evil.example",
    "https://evil.example",
    "javascript:alert(1)",
    "\\/\\/evil.example",
])
def test_next_cannot_send_people_off_site(client, hostile):
    """An open redirect on a sign-in page is a phishing kit: the victim signs
    in on the genuine page and gets handed to a copy that asks again."""
    assert "evil.example" not in client.get("/login", params={"next": hostile}).text
    assert "javascript:alert" not in client.get("/login", params={"next": hostile}).text


def test_next_keeps_a_site_relative_path(client):
    assert "/console/?section=billing" in client.get(
        "/login", params={"next": "/console/?section=billing"}).text


def test_signed_in_visitor_is_sent_on_from_the_sign_in_page(client):
    signup(client, "sarah@bluebonnetbank.com")
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/console/"


def test_signed_in_visitor_cannot_be_redirected_off_site(client):
    signup(client, "sarah@bluebonnetbank.com")
    r = client.get("/login", params={"next": "https://evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/console/"


# --------------------------------------------------------------------------
# Sign out
# --------------------------------------------------------------------------

def test_logout_link_ends_the_session(client):
    signup(client, "sarah@bluebonnetbank.com")
    assert client.get("/api/auth/me").json()["user"] is not None
    r = client.get("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert client.get("/api/auth/me").json()["user"] is None


def test_logout_when_not_signed_in_is_harmless(client):
    r = client.get("/logout", follow_redirects=False)
    assert r.status_code == 303


def test_session_cookie_is_not_readable_by_script(client):
    signup(client, "sarah@bluebonnetbank.com")
    header = client.post("/api/auth/login",
                         json={"email": "sarah@bluebonnetbank.com", "password": PW}
                         ).headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


# --------------------------------------------------------------------------
# Signing up in the middle of something
# --------------------------------------------------------------------------

SITE = """<html><head><title>Bluebonnet Community Bank — Boerne, TX</title></head>
<body><h1>Business Banking</h1>
<h2>Commercial Real Estate Loans</h2><p>Financing for multifamily and retail property.</p>
<h2>Equipment Financing</h2><p>Term loans for equipment purchases.</p>
</body></html>"""


def match_ids(html):
    """Scan the fixture site anonymously and return its template matches."""
    r = TestClient(app).post("/api/ingest/scan",
                             json={"url": "https://bluebonnetbank.com", "html": html})
    r.raise_for_status()
    return [m["id"] for m in r.json()["matches"] if m["template_id"]]


def test_a_scan_survives_being_interrupted_by_signup(client):
    """The funnel this whole flow exists to serve.

    A prospect scans their own website before they have an account. Sign-in is
    a page load now, so the browser carries the run id across in sessionStorage
    and comes back to finish. That only works if the run is still there and
    still claimable by whoever has just signed up — so this pins the server
    half of that handoff, which is the half that can regress quietly.
    """
    anon = TestClient(app)
    run = anon.post("/api/ingest/scan",
                    json={"url": "https://bluebonnetbank.com", "html": SITE})
    run.raise_for_status()
    run_id = run.json()["run_id"]
    match_ids = [m["id"] for m in run.json()["matches"] if m["template_id"]]
    assert match_ids, "the fixture site should match at least one template"

    # Anonymous scanning must stay anonymous: no vendor, nothing owned.
    assert anon.get("/api/auth/me").json()["user"] is None

    # They sign up, which in the browser is a navigation away and back.
    fresh = TestClient(app)
    signup(fresh, "sarah@bluebonnetbank.com")
    built = fresh.post("/api/vendors",
                       json={"name": "Bluebonnet Community Bank", "industry_key": "banking",
                             "website": "bluebonnetbank.com"})
    built.raise_for_status()
    vid = built.json()["vendor"]["id"]

    # And the run they left behind still lands in the new workspace.
    done = fresh.post(f"/api/ingest/{run_id}/confirm",
                      json={"vendor_id": vid, "industry_key": "banking", "match_ids": match_ids})
    done.raise_for_status()
    skills = fresh.get(f"/api/vendors/{vid}").json()["skills"]
    assert len(skills) == len(match_ids), "the scan did not become draft skills"


def test_a_stranger_cannot_claim_a_run_into_someone_elses_workspace(client):
    """The flip side of a run being claimable: it must only be claimable into a
    workspace the claimant actually administers."""
    owner = TestClient(app)
    signup(owner, "owner@bluebonnetbank.com")
    vid = owner.post("/api/vendors", json={"name": "Bluebonnet", "industry_key": "banking"}
                     ).json()["vendor"]["id"]

    run_id = TestClient(app).post(
        "/api/ingest/scan", json={"url": "https://bluebonnetbank.com", "html": SITE}
    ).json()["run_id"]

    outsider = TestClient(app)
    signup(outsider, "someone@else.com")
    r = outsider.post(f"/api/ingest/{run_id}/confirm",
                      json={"vendor_id": vid, "industry_key": "banking", "match_ids": match_ids(SITE)})
    # 404, not 403: this codebase answers for a workspace you are not in
    # exactly as it answers for one that does not exist, so vendor ids cannot
    # be probed by watching which ones come back forbidden.
    assert r.status_code == 404
    assert owner.get(f"/api/vendors/{vid}").json()["skills"] == [], "the refusal still wrote"


# --------------------------------------------------------------------------
# The mailer itself
# --------------------------------------------------------------------------

def test_send_never_raises_even_when_the_transport_is_broken(monkeypatch):
    """A reset email that fails is a person locked out, and the endpoint cannot
    report it without revealing whether the account exists. So a failure has to
    degrade to the outbox rather than become a 500."""
    monkeypatch.setenv("A2B_EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("A2B_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("A2B_SMTP_PORT", "1")
    result = mailer.send_password_reset("sarah@bluebonnetbank.com", "tok")
    assert result["sent"] is False
    assert result["backend"] == "outbox"
    assert "reset-password?token=tok" in outbox()[-1]


def test_ses_uses_the_instance_region_and_restricted_sender(monkeypatch):
    """Production authenticates through the EC2 role, but sender, recipient and
    region still come from our call. Pin all three without touching AWS."""
    called = {}

    class FakeSES:
        def send_email(self, **kwargs):
            called.update(kwargs)

    class FakeBoto3:
        @staticmethod
        def client(service, region_name):
            called["service"] = service
            called["region"] = region_name
            return FakeSES()

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3)
    monkeypatch.setenv("A2B_EMAIL_BACKEND", "ses")
    monkeypatch.setenv("A2B_EMAIL_FROM", "no-reply@lenni.io")
    monkeypatch.setenv("AWS_REGION", "us-east-2")

    result = mailer.send(
        "aditya@alldunkin.com",
        "SES test",
        "Text body",
        "<p>HTML body</p>",
    )

    assert result == {"sent": True, "backend": "ses"}
    assert called["service"] == "ses"
    assert called["region"] == "us-east-2"
    assert called["Source"] == "no-reply@lenni.io"
    assert called["Destination"] == {"ToAddresses": ["aditya@alldunkin.com"]}
