"""The internal portal.

Two things are being tested here, and the first matters more. One is that the
numbers are right. The other is that nobody outside Lenni can reach them —
these are the only routes in the system that read across tenants, so a mistake
here hands one bank another bank's customers.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from a2b import analytics, sessions, skills
from a2b.api.main import app

STAFF = "adi@lenni.io"
OUTSIDER = "owner@bluebonnetbank.com"
PASSWORD = "correct-horse-staple"

ADMIN_ROUTES = ("/api/admin/overview", "/api/admin/companies/x", "/api/admin/runs/x",
                "/api/admin/access-log", "/api/ingest/demand")


@pytest.fixture
def staff_list(monkeypatch):
    monkeypatch.setenv("A2B_STAFF_EMAILS", f" {STAFF.upper()} , someone@lenni.io ")


def sign_up(email: str) -> TestClient:
    client = TestClient(app)
    client.post("/api/auth/signup",
                json={"email": email, "password": PASSWORD}).raise_for_status()
    return client


def start_run(skill_id: str, **kwargs) -> str:
    """A customer run, from a skill id. `start_session` takes the public slugs,
    because that is what a customer's AI arrives with."""
    skill = skills.get_skill(skill_id)
    vendor = skills.get_vendor(skill["vendor_id"])
    return sessions.start_session(vendor_slug=vendor["slug"], skill_slug=skill["slug"],
                                  **kwargs)["id"]


# ---- who gets in ---------------------------------------------------------

def test_a_stranger_is_not_told_the_portal_exists(staff_list):
    """404, not 403. The honest answer tells whoever is probing that there is an
    internal portal here and that getting an address onto a list is the only
    thing between them and every bank's data."""
    client = TestClient(app)

    for route in ADMIN_ROUTES:
        assert client.get(route).status_code == 404, route


def test_a_banks_own_user_gets_the_same_answer(staff_list):
    """The realistic attempt: somebody with a legitimate login who tries the URL
    they saw over our shoulder."""
    client = sign_up(OUTSIDER)

    for route in ADMIN_ROUTES:
        assert client.get(route).status_code == 404, route


def test_staff_get_in(staff_list):
    client = sign_up(STAFF)

    assert client.get("/api/admin/overview").status_code == 200


def test_the_list_is_read_case_and_space_insensitively(staff_list):
    """It is typed into a deploy script by a person, and `A2B_STAFF_EMAILS`
    holding ` Adi@Lenni.io ` should not be the reason nobody can sign in."""
    client = sign_up(STAFF)

    assert client.get("/api/admin/overview").status_code == 200


def test_access_can_be_taken_away_without_touching_the_database(monkeypatch):
    """Why the list is read per call rather than at import. Someone leaves, the
    deploy config changes, and their existing session stops working — without a
    migration and without anyone remembering to delete a row."""
    monkeypatch.setenv("A2B_STAFF_EMAILS", STAFF)
    client = sign_up(STAFF)
    assert client.get("/api/admin/overview").status_code == 200

    monkeypatch.setenv("A2B_STAFF_EMAILS", "")
    assert client.get("/api/admin/overview").status_code == 404


def test_an_empty_staff_list_lets_nobody_in(monkeypatch):
    """The default. A deployment that forgets to configure this has no portal,
    rather than a portal open to every account on it."""
    monkeypatch.delenv("A2B_STAFF_EMAILS", raising=False)
    client = sign_up(STAFF)

    assert client.get("/api/admin/overview").status_code == 404


def test_staff_status_is_not_a_role_a_vendor_can_grant(staff_list, vendor_id):
    """Membership and staff are separate systems on purpose. Making somebody an
    admin of your own company must not make them ours."""
    client = sign_up(OUTSIDER)
    skills.add_member(vendor_id, OUTSIDER, role="admin", status="active")

    assert client.get("/api/admin/overview").status_code == 404


# ---- what it reports -----------------------------------------------------

def test_the_overview_sees_every_company_not_just_the_callers(staff_list, vendor_id):
    """The whole point. `/api/vendors` deliberately returns only what the caller
    belongs to, and this is the one place that rule is inverted."""
    other = skills.create_vendor(name="Second Bank", industry_key="banking",
                                 website="second.example", description="")
    client = sign_up(STAFF)

    names = {c["name"] for c in client.get("/api/admin/overview").json()["companies"]}

    assert {"Bluebonnet Community Bank", "Second Bank"} <= names
    assert other  # created outside any membership of the caller


def test_a_company_with_no_customers_is_not_counted_as_live(staff_list, vendor_id):
    """A workspace full of products looks identical to a working integration
    until you ask whether a customer has ever started one."""
    skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    client = sign_up(STAFF)

    row = next(c for c in client.get("/api/admin/overview").json()["companies"]
               if c["id"] == vendor_id)

    assert row["products"] == 1
    assert row["live"] is False
    assert row["runs"]["total"] == 0


def test_a_real_run_moves_the_numbers(staff_list, published_skill, vendor_id):
    start_run(published_skill, terminal="claude")
    client = sign_up(STAFF)

    body = client.get("/api/admin/overview").json()
    row = next(c for c in body["companies"] if c["id"] == vendor_id)

    assert row["live"] is True
    assert row["runs"]["total"] == 1
    assert body["totals"]["runs"] >= 1
    assert row["last_active"]


def test_the_overview_counts_products_still_waiting_to_be_fetched(staff_list, vendor_id):
    """The number that would have caught #86 months earlier. From inside the
    company that owns it a fetch-based product looks entirely healthy; it is
    only visible as a problem when you can see how few of them there are."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_entry_mode(sid, "staged")
    client = sign_up(STAFF)

    assert client.get("/api/admin/overview").json()["entry_modes"]["staged"] == 1


def test_drop_off_is_read_from_the_run_not_from_old_events(staff_list, published_skill):
    """A vendor who deletes a section leaves completion events behind pointing at
    something that no longer exists. The run's own status is reconciled against
    the live sections, so it answers the way the customer experienced it."""
    sid = start_run(published_skill)
    skill = skills.get_skill(published_skill)
    sessions.complete_section(sid, skill["sections"][0]["id"], {"collateral_type": "retail"})
    client = sign_up(STAFF)

    detail = client.get(f"/api/admin/companies/{skill['vendor_id']}").json()
    dropoff = detail["products"][0]["dropoff"]

    assert dropoff[0]["completed"] == 1
    assert dropoff[0]["of"] == 1
    assert dropoff[1]["completed"] == 0


# ---- the answers themselves ---------------------------------------------

def test_staff_can_read_what_a_customer_entered(staff_list, published_skill):
    """Asked for deliberately: we run support on this system and cannot answer
    "what did my customer see" from counts alone."""
    sid = start_run(published_skill)
    skill = skills.get_skill(published_skill)
    sessions.complete_section(sid, skill["sections"][0]["id"],
                              {"stated_value": "$2.4M", "collateral_type": "retail strip"})
    client = sign_up(STAFF)

    body = client.get(f"/api/admin/runs/{sid}").json()

    assert body["run"]["facts"]["stated_value"] == "$2.4M"
    assert body["skill"]["name"] == skill["name"]


def test_opening_a_customers_answers_is_written_down(staff_list, published_skill):
    """The condition attached to that access. A bank asking who at Lenni saw
    their applicant's file needs an answer that is not "we would have to ask
    around"."""
    sid = start_run(published_skill)
    client = sign_up(STAFF)

    client.get(f"/api/admin/runs/{sid}")
    log = client.get("/api/admin/access-log").json()["access"]

    assert any(a["email"] == STAFF and a["what"] == "run" and a["session_id"] == sid
               for a in log)


def test_the_audit_trail_is_not_written_into_the_banks_own_numbers(staff_list,
                                                                   published_skill,
                                                                   vendor_id):
    """Our support visits are not the customer's journey. A bank reading their
    own funnel should not find us in it."""
    sid = start_run(published_skill)
    client = sign_up(STAFF)
    client.get(f"/api/admin/runs/{sid}")

    funnel = sessions.funnel_summary(vendor_id)["events"]

    assert not any("staff" in event or "access" in event for event in funnel)


def test_a_missing_run_says_the_same_thing_as_a_forbidden_one(staff_list):
    client = sign_up(STAFF)

    assert client.get("/api/admin/runs/ses_does_not_exist").status_code == 404


# ---- the module on its own -----------------------------------------------

def test_the_portfolio_query_does_not_grow_with_the_number_of_companies(vendor_id):
    """A loop calling funnel_summary() per company reads better and turns this
    page into a query per company per table. Fine at ten, discovered at four
    hundred, on the day someone senior is looking at it."""
    for i in range(6):
        skills.create_vendor(name=f"Bank {i}", industry_key="banking",
                             website=f"bank{i}.example", description="")

    calls = []
    original = analytics.cursor

    class Counting:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            calls.append(1)
            return self._inner.__enter__()

        def __exit__(self, *a):
            return self._inner.__exit__(*a)

    analytics.cursor = lambda: Counting(original())
    try:
        analytics.platform()
    finally:
        analytics.cursor = original

    assert len(calls) == 1, f"the portfolio view opened {len(calls)} connections"
