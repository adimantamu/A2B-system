"""Authentication and tenant isolation.

The point of these tests is not that login works — that is the easy half. It is
that a signed-in user of vendor A cannot see or touch vendor B, which is the
property that makes this safe to put on a public address.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from a2b import auth
from a2b.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def signup(email, password="correct-horse-staple", name=""):
    """A fresh client with its own cookie jar, signed in as this user."""
    c = TestClient(app)
    r = c.post("/api/auth/signup", json={"email": email, "password": password, "name": name})
    r.raise_for_status()
    return c


def make_vendor(c, name, industry="banking"):
    return c.post("/api/vendors", json={"name": name, "industry_key": industry}).json()["vendor"]


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------

def test_password_hash_is_salted_and_verifiable():
    a, b = auth.hash_password("hunter2hunter2"), auth.hash_password("hunter2hunter2")
    assert a != b, "same password must not produce the same hash"
    assert "hunter2hunter2" not in a
    assert auth.verify_password("hunter2hunter2", a)
    assert not auth.verify_password("hunter2hunter3", a)


def test_short_passwords_are_refused(client):
    r = client.post("/api/auth/signup", json={"email": "x@y.com", "password": "short"})
    assert r.status_code == 400


def test_duplicate_signup_is_refused(client):
    signup("dupe@example.com")
    r = client.post("/api/auth/signup",
                    json={"email": "dupe@example.com", "password": "correct-horse-staple"})
    assert r.status_code == 409


def test_wrong_password_says_nothing_about_whether_the_account_exists(client):
    signup("real@example.com")
    known = client.post("/api/auth/login",
                        json={"email": "real@example.com", "password": "wrong-wrong-wrong"})
    unknown = client.post("/api/auth/login",
                          json={"email": "ghost@example.com", "password": "wrong-wrong-wrong"})
    assert known.status_code == unknown.status_code == 401
    assert known.json()["detail"] == unknown.json()["detail"]


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

def test_session_cookie_is_httponly_and_the_raw_token_is_not_stored(client):
    r = client.post("/api/auth/signup",
                    json={"email": "cookie@example.com", "password": "correct-horse-staple"})
    header = r.headers["set-cookie"]
    assert "HttpOnly" in header and "SameSite=lax" in header.replace("Lax", "lax")

    token = client.cookies[auth.COOKIE_NAME]
    from a2b.db import cursor
    with cursor() as cur:
        stored = [row["token_hash"] for row in cur.execute("SELECT token_hash FROM auth_session")]
    assert token not in stored, "the database must hold a hash, not a usable token"


def test_logout_invalidates_the_session(client):
    c = signup("bye@example.com")
    assert c.get("/api/auth/me").json()["user"]["email"] == "bye@example.com"
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").json()["user"] is None
    assert c.get("/api/vendors").status_code == 401


def test_me_is_anonymous_rather_than_401_when_signed_out(client):
    body = client.get("/api/auth/me").json()
    assert body == {"user": None, "vendors": []}


def test_changing_the_password_ends_other_sessions():
    laptop = signup("two@example.com")
    phone = TestClient(app)
    phone.post("/api/auth/login",
               json={"email": "two@example.com", "password": "correct-horse-staple"})
    assert phone.get("/api/auth/me").json()["user"] is not None

    laptop.post("/api/auth/password", json={
        "current_password": "correct-horse-staple", "new_password": "a-brand-new-secret",
    }).raise_for_status()

    assert phone.get("/api/auth/me").json()["user"] is None, "the other device must be signed out"
    assert laptop.get("/api/auth/me").json()["user"] is not None, "but not the one that changed it"


# --------------------------------------------------------------------------
# Tenant isolation — the part that matters
# --------------------------------------------------------------------------

def test_anonymous_callers_cannot_reach_the_authoring_api(client):
    assert client.get("/api/vendors").status_code == 401
    assert client.post("/api/vendors",
                       json={"name": "Sneaky", "industry_key": "banking"}).status_code == 401


def test_a_vendor_list_contains_only_your_own():
    alice, bob = signup("alice@a.com"), signup("bob@b.com")
    make_vendor(alice, "Alice Bank")
    make_vendor(bob, "Bob Bank")

    assert [v["name"] for v in alice.get("/api/vendors").json()["vendors"]] == ["Alice Bank"]
    assert [v["name"] for v in bob.get("/api/vendors").json()["vendors"]] == ["Bob Bank"]


def test_another_tenants_vendor_is_not_readable():
    alice, bob = signup("alice2@a.com"), signup("bob2@b.com")
    v = make_vendor(alice, "Alice Second Bank")
    # 404, not 403: confirming the id is real is itself a leak.
    assert bob.get(f"/api/vendors/{v['id']}").status_code == 404


def test_another_tenants_skill_cannot_be_read_or_edited():
    alice, bob = signup("alice3@a.com"), signup("bob3@b.com")
    v = make_vendor(alice, "Alice Third Bank")
    skill = alice.post(f"/api/vendors/{v['id']}/skills/seed",
                       params={"template_id": "uw-multifamily-perm"}).json()["skill"]
    sid = skill["id"]
    section_id = skill["sections"][0]["id"]

    assert bob.get(f"/api/skills/{sid}").status_code == 404
    assert bob.patch(f"/api/skills/{sid}", params={"name": "Owned"}).status_code == 404
    assert bob.post(f"/api/skills/{sid}/publish").status_code == 404
    assert bob.delete(f"/api/skills/{sid}").status_code == 404
    assert bob.put(f"/api/sections/{section_id}", json={"says": "hi"}).status_code == 404
    assert bob.post(f"/api/sections/{section_id}/move", params={"delta": 1}).status_code == 404

    # And none of it landed.
    assert alice.get(f"/api/skills/{sid}").json()["skill"]["name"] != "Owned"


def test_ingest_cannot_be_confirmed_into_someone_elses_vendor():
    alice, bob = signup("alice4@a.com"), signup("bob4@b.com")
    v = make_vendor(alice, "Alice Fourth Bank")
    scan = bob.post("/api/ingest/scan", json={
        "url": "https://x.com", "html": "<h2>Commercial real estate loans</h2>",
    }).json()
    r = bob.post(f"/api/ingest/{scan['run_id']}/confirm",
                 json={"vendor_id": v["id"], "match_ids": [m["id"] for m in scan["matches"]]})
    assert r.status_code == 404
    assert alice.get(f"/api/vendors/{v['id']}").json()["skills"] == []


def test_scanning_is_open_because_it_happens_before_signup(client):
    """A prospective vendor pastes their URL before they have an account. The
    run is written with no vendor_id and is inert until someone confirms it."""
    r = client.post("/api/ingest/scan", json={
        "url": "https://prospect.com", "html": "<h2>Commercial real estate loans</h2>",
    })
    assert r.status_code == 200
    assert r.json()["matches"]


# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------

def test_a_viewer_can_read_but_not_write():
    admin = signup("admin5@a.com")
    signup("viewer5@a.com")
    v = make_vendor(admin, "Role Bank")
    admin.post(f"/api/vendors/{v['id']}/members",
               json={"email": "viewer5@a.com", "role": "viewer"}).raise_for_status()
    skill = admin.post(f"/api/vendors/{v['id']}/skills/seed",
                       params={"template_id": "uw-multifamily-perm"}).json()["skill"]

    viewer = TestClient(app)
    viewer.post("/api/auth/login",
                json={"email": "viewer5@a.com", "password": "correct-horse-staple"})

    assert viewer.get(f"/api/vendors/{v['id']}").status_code == 200
    assert viewer.get(f"/api/skills/{skill['id']}").status_code == 200
    # Writes are refused, and as a member they get a real 403 rather than a 404.
    assert viewer.patch(f"/api/skills/{skill['id']}", params={"name": "No"}).status_code == 404
    assert viewer.post(f"/api/vendors/{v['id']}/skills/custom",
                       params={"name": "Nope"}).status_code == 403


def test_an_editor_cannot_delete_a_skill_or_manage_the_team():
    admin = signup("admin6@a.com")
    signup("editor6@a.com")
    v = make_vendor(admin, "Editor Bank")
    admin.post(f"/api/vendors/{v['id']}/members", json={"email": "editor6@a.com", "role": "editor"})
    skill = admin.post(f"/api/vendors/{v['id']}/skills/seed",
                       params={"template_id": "uw-multifamily-perm"}).json()["skill"]

    editor = TestClient(app)
    editor.post("/api/auth/login",
                json={"email": "editor6@a.com", "password": "correct-horse-staple"})

    assert editor.patch(f"/api/skills/{skill['id']}", params={"name": "Renamed"}).status_code == 200
    assert editor.delete(f"/api/skills/{skill['id']}").status_code == 404
    assert editor.post(f"/api/vendors/{v['id']}/members",
                       json={"email": "x@y.com", "role": "editor"}).status_code == 403


def test_an_invite_becomes_access_when_the_invitee_signs_up():
    admin = signup("admin7@a.com")
    v = make_vendor(admin, "Invite Bank")
    admin.post(f"/api/vendors/{v['id']}/members",
               json={"email": "later@a.com", "role": "editor"}).raise_for_status()

    members = admin.get(f"/api/vendors/{v['id']}").json()["members"]
    assert next(m for m in members if m["email"] == "later@a.com")["status"] == "invited"

    invitee = signup("later@a.com")
    assert [x["name"] for x in invitee.get("/api/vendors").json()["vendors"]] == ["Invite Bank"]


def test_the_last_admin_cannot_be_removed():
    admin = signup("admin8@a.com")
    v = make_vendor(admin, "Solo Bank")
    r = admin.request("DELETE", f"/api/vendors/{v['id']}/members",
                      params={"email": "admin8@a.com"})
    assert r.status_code == 409


def test_the_creator_becomes_an_admin_of_what_they_create():
    alice = signup("alice9@a.com")
    v = make_vendor(alice, "Ninth Bank")
    assert alice.get(f"/api/vendors/{v['id']}").json()["role"] == "admin"


# --------------------------------------------------------------------------
# The public surface stays public
# --------------------------------------------------------------------------

def test_auth_does_not_close_the_public_doors(client):
    """A customer's AI has no account here. If auth reached the manifest or the
    MCP endpoint, the whole product would stop working."""
    assert client.get("/health").status_code == 200
    assert client.get("/mcp/tools").status_code == 200
    assert client.get("/api/registry/apps").status_code == 200
    assert client.get("/api/registry/industries").status_code == 200
    assert client.get("/api/registry/library").status_code == 200
    assert client.get("/api/registry/templates").status_code == 200
    assert client.get("/api/registry/products").status_code == 200

    alice = signup("alice10@a.com")
    v = make_vendor(alice, "Public Bank")
    skill = alice.post(f"/api/vendors/{v['id']}/skills/seed",
                       params={"template_id": "uw-multifamily-perm"}).json()["skill"]
    alice.post(f"/api/skills/{skill['id']}/connect-convey",
               params={"url": "https://public.convey.lenni.io"})
    alice.post(f"/api/skills/{skill['id']}/publish").raise_for_status()

    slug = v["slug"]
    assert client.get(f"/v/{slug}/llms.txt").status_code == 200
    assert client.get(f"/v/{slug}/agents").status_code == 200
    assert client.get(f"/v/{slug}/start/multifamily-perm").status_code == 200

    started = client.post("/mcp/call", json={
        "name": "lenni_start_session",
        "arguments": {"vendor": slug, "skill": "multifamily-perm",
                      "email": "customer@example.com"},
    }).json()
    assert "result" in started, started
