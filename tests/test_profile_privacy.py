"""What a stranger can learn about a customer from the open MCP endpoint.

Every test here is written from the attacker's side and holds nothing but a
guessed email address, because that is the whole of what the hole in #24
required. The endpoint has to stay open — an arriving customer's AI has no
account here and never will — so these are the tests that stand in for a
credential we cannot ask for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from a2b import sessions, skills  # noqa: E402
from a2b.api.main import app  # noqa: E402
from a2b.destination import build_markdown  # noqa: E402

VICTIM = "borrower@example.com"
STRANGER = "nobody-has-this-address@example.com"


@pytest.fixture
def client():
    """Anonymous throughout. No signup, no cookie, nothing."""
    return TestClient(app)


@pytest.fixture
def victim_with_history(published_skill):
    """A customer who has told a vendor real things and gone home."""
    skill = skills.get_skill(published_skill)
    sid = sessions.start_session(vendor_slug="bluebonnet-community-bank",
                                 skill_slug=skill["slug"], email=VICTIM)["id"]
    sessions.complete_section(sid, sessions.current_section(sid)["section_id"],
                              {"entity": "River Rd Holdings LLC",
                               "property_address": "1400 River Rd, Boerne TX",
                               "annual_income": "510000"})
    return sid


def call(client, name, arguments):
    return client.post("/mcp/call", json={"name": name, "arguments": arguments}).json()


def test_an_email_no_longer_buys_a_profile(client, victim_with_history):
    """The read the issue was filed about."""
    got = call(client, "lenni_get_user_context", {"email": VICTIM})
    assert "result" not in got, "an anonymous caller read a profile"
    assert got["error"]["code"] == "unknown_tool"


def test_a_stranger_cannot_tell_who_has_a_profile(client, victim_with_history):
    """The half that mattered more, and the reason a rate limit was not enough.

    Reading facts needed a correct guess at an address. Learning whether an
    address *had* a profile needed only the address, and that turns a mailing
    list into a list of who else banks with whom — without reading one fact.

    The two responses have to be identical, so this compares them rather than
    asserting on either alone.
    """
    known = call(client, "lenni_get_user_context", {"email": VICTIM})
    unknown = call(client, "lenni_get_user_context", {"email": STRANGER})
    assert known == unknown


def test_enumeration_finds_nothing_to_sort_on(client, victim_with_history):
    """The enumeration attempt the acceptance criteria ask for, run in full.

    A real attacker does not stop at one address; they walk a list and keep the
    ones that answer differently. This walks a list containing the victim and
    keeps nothing, because there is nothing to sort on.
    """
    guesses = [VICTIM, VICTIM.upper(), STRANGER, "owner@bluebonnetbank.com",
               "sarah@bluebonnetbank.com", "admin@example.com"]
    answers = {client.post("/mcp/call", json={
        "name": "lenni_get_user_context", "arguments": {"email": g},
    }).text for g in guesses}
    assert len(answers) == 1, f"replies differ by address, so they sort: {answers}"


def test_starting_a_session_as_someone_else_inherits_nothing(client, published_skill,
                                                             victim_with_history):
    """Closing the read alone would have left the door open beside it.

    `lenni_start_session` also takes an email, and it used to copy that profile's
    facts into the new session — so a stranger could ignore the missing tool
    entirely and start a run carrying someone else's answers.

    Where those answers went is the part worth being exact about. They do not
    come back in the reply: `finish` hands over file paths, not contents, and no
    route serves the package directory, so nobody downloads them. They went into
    the assembled package — the document a bank is then asked to act on. So the
    damage was not a stranger reading the facts; it was a stranger causing one
    person's private answers to be delivered to a lender of the stranger's
    choosing, under that person's name.

    This asserts at the package rather than at the response body, because the
    response body never held them and a test written against it passes on the
    vulnerable code.
    """
    started = call(client, "lenni_start_session", {
        "vendor": "bluebonnet-community-bank",
        "skill": skills.get_skill(published_skill)["slug"],
        "email": VICTIM, "terminal": "chatgpt",
    })["result"]
    sid = started["session_id"]

    assert sessions.get_session(sid)["facts"] == {}

    package = build_markdown(sid)
    for secret in ("River Rd Holdings", "1400 River Rd", "510000"):
        assert secret not in package, f"{secret!r} was assembled into a stranger's package"


def test_a_stranger_cannot_overwrite_someone_elses_profile(client, victim_with_history):
    """The write side, which is quieter and worse.

    Saving used to flow back to the profile the session was started under, so the
    same guessed address let a stranger edit what a bank would later be told.
    """
    started = call(client, "lenni_start_session", {
        "vendor": "bluebonnet-community-bank", "skill": "multifamily-perm",
        "email": VICTIM,
    })["result"]
    call(client, "lenni_save_deal_facts", {
        "session_id": started["session_id"],
        "facts": {"annual_income": "1", "entity": "Not Their Company"},
    })

    profile = sessions.get_profile_by_email(VICTIM)
    assert profile["facts"] == {}, "a stranger's answers reached the victim's profile"


def test_the_customer_in_front_of_you_still_gets_their_own_answers(client, published_skill):
    """The check that this is a fix and not just a removal.

    Everything above would also pass if sessions had stopped recording facts at
    all, so this is the other half: within one run, what the customer says is
    kept, and the vendor still knows which address to reply to.
    """
    started = call(client, "lenni_start_session", {
        "vendor": "bluebonnet-community-bank",
        "skill": skills.get_skill(published_skill)["slug"],
        "email": "new-customer@example.com",
    })["result"]
    sid = started["session_id"]
    call(client, "lenni_save_deal_facts", {
        "session_id": sid, "facts": {"entity": "Their Own LLC"},
    })

    assert sessions.get_session(sid)["facts"]["entity"] == "Their Own LLC"
    profile = sessions.get_profile_by_email("new-customer@example.com")
    assert profile is not None, "the vendor lost the customer's contact record"
