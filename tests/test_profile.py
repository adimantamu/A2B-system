"""The account page: the person, not the company.

Two properties matter more than the CRUD. The first is that the email is not
editable, because `vendor_member` is keyed by address — changing it would strand
every invite silently, and a test is the only thing that stops someone adding
the field later because it looks like an oversight. The second is that a profile
is reachable only by its owner: the console has no route that takes a user id,
and it must stay that way.
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
    c = TestClient(app)
    r = c.post("/api/auth/signup", json={"email": email, "password": password, "name": name})
    r.raise_for_status()
    return c


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def test_profile_carries_what_me_does_not():
    """`/auth/me` reads the session; the account page needs the row."""
    c = signup("reader@example.com", name="Ada Reader")
    body = c.get("/api/auth/profile").json()

    assert body["user"]["email"] == "reader@example.com"
    assert body["user"]["name"] == "Ada Reader"
    assert body["user"]["created_at"], "the page shows 'member since'"
    assert "last_login_at" in body["user"]
    assert body["sessions"] >= 1
    assert body["email_editable"] is False


def test_profile_never_returns_the_password_hash():
    c = signup("secrets@example.com")
    assert "password_hash" not in c.get("/api/auth/profile").json()["user"]


def test_profile_needs_a_session(client):
    assert client.get("/api/auth/profile").status_code == 401
    assert client.post("/api/auth/profile", json={"name": "Nobody"}).status_code == 401


def test_profile_lists_the_workspaces_this_person_can_reach():
    c = signup("owner@example.com")
    c.post("/api/vendors", json={"name": "Bluebonnet Bank", "industry_key": "banking"})
    body = c.get("/api/auth/profile").json()
    assert [v["name"] for v in body["vendors"]] == ["Bluebonnet Bank"]
    assert body["vendors"][0]["role"] == "admin"


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def test_name_can_be_changed_and_comes_back_changed():
    c = signup("rename@example.com", name="Old Name")
    body = c.post("/api/auth/profile", json={"name": "New Name"}).json()
    assert body["user"]["name"] == "New Name"
    assert c.get("/api/auth/profile").json()["user"]["name"] == "New Name"


def test_name_is_trimmed_and_cannot_be_blanked():
    c = signup("blank@example.com", name="Has A Name")
    assert c.post("/api/auth/profile", json={"name": "  Spaced  "}).json()["user"]["name"] == "Spaced"
    assert c.post("/api/auth/profile", json={"name": "   "}).status_code == 400
    assert c.get("/api/auth/profile").json()["user"]["name"] == "Spaced"


def test_the_email_is_not_editable_through_the_profile():
    """Membership is keyed by email. If this ever starts working, invites break."""
    c = signup("fixed@example.com", name="Fixed")
    c.post("/api/auth/profile", json={"name": "Fixed", "email": "moved@example.com"})
    assert c.get("/api/auth/profile").json()["user"]["email"] == "fixed@example.com"
    assert auth.user_exists("fixed@example.com")
    assert not auth.user_exists("moved@example.com")


def test_a_profile_edit_does_not_touch_anybody_else():
    a = signup("a-edits@example.com", name="A")
    b = signup("b-untouched@example.com", name="B")
    a.post("/api/auth/profile", json={"name": "A Changed"})
    assert b.get("/api/auth/profile").json()["user"]["name"] == "B"


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

def test_signing_out_other_browsers_keeps_the_one_that_asked():
    first = signup("multi@example.com")
    second = TestClient(app)
    second.post("/api/auth/login",
                json={"email": "multi@example.com", "password": "correct-horse-staple"})
    assert first.get("/api/auth/profile").json()["sessions"] == 2

    ended = first.post("/api/auth/sessions/revoke-others").json()
    assert ended["ended"] == 1
    assert ended["sessions"] == 1

    assert first.get("/api/auth/profile").status_code == 200, "the caller stayed signed in"
    assert second.get("/api/auth/profile").status_code == 401, "the other browser is out"


def test_changing_the_password_keeps_this_tab_and_drops_the_rest():
    first = signup("rotate@example.com")
    second = TestClient(app)
    second.post("/api/auth/login",
                json={"email": "rotate@example.com", "password": "correct-horse-staple"})

    r = first.post("/api/auth/password", json={"current_password": "correct-horse-staple",
                                               "new_password": "a-brand-new-passphrase"})
    assert r.status_code == 200
    assert first.get("/api/auth/profile").status_code == 200
    assert second.get("/api/auth/profile").status_code == 401
    assert first.get("/api/auth/profile").json()["sessions"] == 1


def test_the_wrong_current_password_changes_nothing():
    c = signup("careful@example.com")
    r = c.post("/api/auth/password", json={"current_password": "not-the-password",
                                           "new_password": "a-brand-new-passphrase"})
    assert r.status_code == 401
    assert c.get("/api/auth/profile").status_code == 200, "the caller was not signed out"
    fresh = TestClient(app)
    assert fresh.post("/api/auth/login",
                      json={"email": "careful@example.com",
                            "password": "correct-horse-staple"}).status_code == 200
