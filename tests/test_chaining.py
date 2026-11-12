"""Section chaining, section_status, and the editing rules around it."""
from __future__ import annotations

import pytest

from a2b import sessions, skills


def test_a_draft_skill_cannot_be_run(vendor_id):
    """Drafts are invisible to customers and agents. Publishing is the consent
    moment, so starting a session against a draft has to be refused."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skill = skills.get_skill(sid)
    with pytest.raises(PermissionError):
        sessions.start_session(vendor_slug="bluebonnet-community-bank",
                               skill_slug=skill["slug"])


def test_session_walks_every_section_in_order(published_skill):
    skill = skills.get_skill(published_skill)
    session = sessions.start_session(vendor_slug="bluebonnet-community-bank",
                                     skill_slug=skill["slug"], email="dev@example.com")
    sid = session["id"]

    section = sessions.current_section(sid)
    assert section["name"] == "Collateral"
    assert section["position"] == 1 and section["of"] == 3
    assert section["next"]["name"] == "Deal"

    step = sessions.complete_section(sid, section["section_id"], {"address": "400 River Rd"})
    assert step["done"] is False
    assert step["section"]["name"] == "Deal"

    step = sessions.complete_section(sid, step["section"]["section_id"])
    assert step["section"]["name"] == "Borrower"
    assert step["section"]["is_last"] is True
    assert step["section"]["on_finish"]  # tells the AI where the package goes

    step = sessions.complete_section(sid, step["section"]["section_id"])
    assert step["done"] is True
    assert sessions.get_session(sid)["status"] == "complete"


def test_progress_tracks_completion(published_skill):
    skill = skills.get_skill(published_skill)
    sid = sessions.start_session(vendor_slug="bluebonnet-community-bank",
                                 skill_slug=skill["slug"])["id"]
    assert sessions.progress(sid)["fraction"] == 0.0

    sessions.complete_section(sid, sessions.current_section(sid)["section_id"])
    p = sessions.progress(sid)
    assert p["completed"] == 1 and p["total"] == 3


def test_status_is_keyed_by_section_id_so_mid_run_edits_are_safe(published_skill):
    """A vendor may add a section while a customer is part-way through. An
    index-keyed status would point at the wrong section after that; the new
    section should simply join as pending."""
    skill = skills.get_skill(published_skill)
    sid = sessions.start_session(vendor_slug="bluebonnet-community-bank",
                                 skill_slug=skill["slug"])["id"]
    first = sessions.current_section(sid)
    sessions.complete_section(sid, first["section_id"])

    skills.add_library_section(published_skill, "banking", 1)

    p = sessions.progress(sid)
    assert p["total"] == 4
    assert p["completed"] == 1
    assert p["sections"][0]["status"] == "complete"
    assert sessions.current_section(sid)["name"] == "Deal"


def test_removing_the_active_section_mid_run_does_not_strand_the_session(published_skill):
    """The customer is sitting on a section when the vendor deletes it. The
    session has to survive that, not point at a row that is gone."""
    skill = skills.get_skill(published_skill)
    sid = sessions.start_session(vendor_slug="bluebonnet-community-bank",
                                 skill_slug=skill["slug"])["id"]
    added = skills.add_library_section(published_skill, "banking", 1)

    for s in skills.get_skill(published_skill)["sections"][:3]:
        sessions.complete_section(sid, s["id"])
    assert sessions.current_section(sid)["section_id"] == added

    skills.delete_section(added)

    progress = sessions.progress(sid)
    assert progress["total"] == 3
    assert progress["completed"] == 3
    assert sessions.current_section(sid) is None


def test_core_sections_cannot_be_deleted(vendor_id):
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    core = skills.get_skill(sid)["sections"][0]
    assert core["is_core"] is True
    with pytest.raises(ValueError, match="core section"):
        skills.delete_section(core["id"])


def test_added_sections_can_be_deleted(vendor_id):
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    added = skills.add_library_section(sid, "banking", 0)
    skills.delete_section(added)
    assert added not in [s["id"] for s in skills.get_skill(sid)["sections"]]


def test_cross_industry_section_carries_an_origin_tag(vendor_id):
    """A bank borrowing Legal's estate inventory should see where it came from;
    a bank adding a banking section should not be told it came from Banking."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    legal = skills.add_library_section(sid, "legal", 2)
    banking = skills.add_library_section(sid, "banking", 0)

    by_id = {s["id"]: s for s in skills.get_skill(sid)["sections"]}
    assert by_id[legal]["origin_industry"] == "legal"
    assert by_id[banking]["origin_industry"] is None


def test_validate_reports_gaps_without_judging_the_deal(published_skill):
    skill = skills.get_skill(published_skill)
    sid = sessions.start_session(vendor_slug="bluebonnet-community-bank",
                                 skill_slug=skill["slug"])["id"]
    check = sessions.validate_package(sid)
    assert check["complete"] is False
    assert check["gap_count"] == 3
    assert check["next_question"]

    for s in skill["sections"]:
        sessions.complete_section(sid, s["id"])
    assert sessions.validate_package(sid)["complete"] is True


def test_facts_do_not_follow_an_unproven_email_to_the_next_vendor(published_skill):
    """Portability is switched off until the address can be proved — #15, #24.

    This is the test that used to assert the opposite, and it is left pointing at
    the same journey on purpose: the second vendor is a *different* vendor, so
    what travelled between them was one person's private facts, carried on an
    email address anyone could type.
    """
    skill = skills.get_skill(published_skill)
    sid = sessions.start_session(vendor_slug="bluebonnet-community-bank",
                                 skill_slug=skill["slug"], email="borrower@example.com")["id"]
    sessions.complete_section(sid, sessions.current_section(sid)["section_id"],
                              {"entity": "River Rd Holdings LLC"})

    other = skills.create_vendor(name="Second Bank", industry_key="banking")
    other_skill = skills.seed_skill_from_template(other, "uw-cre-owner-occupied")
    skills.connect_convey(other_skill, "https://second.convey.lenni.io")
    skills.publish_skill(other_skill)

    # The run that collected the fact still has it. Nothing about this change
    # costs a customer the answers they are in the middle of giving.
    assert sessions.get_session(sid)["facts"]["entity"] == "River Rd Holdings LLC"

    # The profile did not absorb it, so there is nothing at the second vendor to
    # inherit and nothing for a stranger to read back.
    profile = sessions.get_profile_by_email("borrower@example.com")
    assert profile is not None, "the contact record should still exist"
    assert profile["facts"] == {}

    s2 = sessions.start_session(vendor_slug=skills.get_vendor(other)["slug"],
                                skill_slug=skills.get_skill(other_skill)["slug"],
                                email="borrower@example.com")
    assert sessions.get_session(s2["id"])["facts"] == {}


def test_finish_assembles_outputs_and_a_closing_message(published_skill):
    skill = skills.get_skill(published_skill)
    sid = sessions.start_session(vendor_slug="bluebonnet-community-bank",
                                 skill_slug=skill["slug"])["id"]
    for s in skill["sections"]:
        sessions.complete_section(sid, s["id"])

    result = sessions.finish(sid)
    assert result["validated"] is True
    assert result["outputs"]["markdown"]
    assert isinstance(result["outputs"]["doc_list"], list)
    assert "Convey" in result["destination"]
    assert result["closing_message"]
