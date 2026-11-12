"""Version pinning — locked model rule 1.

These are the highest-value tests in the suite. A regression here silently
rewrites words a vendor has approved for talking to their own customers, and it
would do so without any user action to point at afterwards.
"""
from __future__ import annotations

import pytest

from a2b import registry, skills


def test_seed_records_the_pin(vendor_id):
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skill = skills.get_skill(sid)
    status = skill["template_status"]
    assert status["template_id"] == "uw-multifamily-perm"
    assert status["pinned_version"] == "1.0"
    assert status["update_available"] is False


def test_seed_is_a_deep_copy_not_a_reference(vendor_id):
    """Editing a vendor's section must not reach back into the template."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    section = skills.get_skill(sid)["sections"][0]
    skills.update_section(section["id"], says="Bluebonnet's own words.")

    assert skills.get_skill(sid)["sections"][0]["says"] == "Bluebonnet's own words."
    assert registry.template("uw-multifamily-perm").sections[0].says != "Bluebonnet's own words."


def test_two_vendors_seeded_from_one_template_are_independent():
    v1 = skills.create_vendor(name="First Bank", industry_key="banking")
    v2 = skills.create_vendor(name="Second Bank", industry_key="banking")
    s1 = skills.seed_skill_from_template(v1, "uw-cre-owner-occupied")
    s2 = skills.seed_skill_from_template(v2, "uw-cre-owner-occupied")

    skills.update_section(skills.get_skill(s1)["sections"][0]["id"], says="First Bank voice.")

    assert skills.get_skill(s1)["sections"][0]["says"] == "First Bank voice."
    assert skills.get_skill(s2)["sections"][0]["says"] != "First Bank voice."


def test_shipping_a_new_template_version_does_not_move_a_seeded_skill(vendor_id, monkeypatch):
    """Lenni ships T+1. The vendor's skill stays exactly where it was."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    before = skills.get_skill(sid)
    original_says = before["sections"][0]["says"]

    _ship_new_version(monkeypatch, "uw-multifamily-perm", "1.1", "Rewritten upstream opening.")

    after = skills.get_skill(sid)
    assert after["sections"][0]["says"] == original_says, "pinned content moved on its own"
    assert after["template_status"]["pinned_version"] == "1.0"
    assert after["template_status"]["available_version"] == "1.1"
    # The UI surfaces this. It never acts on it.
    assert after["template_status"]["update_available"] is True


def test_adopting_a_version_is_explicit_and_keeps_added_sections(vendor_id, monkeypatch):
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.add_library_section(sid, "banking", 0)  # "Insurance quote"
    _ship_new_version(monkeypatch, "uw-multifamily-perm", "1.1", "Rewritten upstream opening.")

    diff = skills.template_diff(sid)
    assert diff["update_available"] is True
    assert diff["from_version"] == "1.0" and diff["to_version"] == "1.1"

    updated = skills.adopt_template_version(sid, keep_added_sections=True)
    assert updated["sections"][0]["says"] == "Rewritten upstream opening."
    assert updated["template_status"]["pinned_version"] == "1.1"
    assert "Insurance quote" in [s["name"] for s in updated["sections"]]


def test_adopt_refuses_on_a_custom_skill(vendor_id):
    sid = skills.create_custom_skill(vendor_id, "Trust Services")
    with pytest.raises(ValueError, match="not seeded from a template"):
        skills.adopt_template_version(sid)


def _ship_new_version(monkeypatch, template_id: str, version: str, new_says: str) -> None:
    """Simulate Lenni publishing a new template version."""
    from dataclasses import replace

    original = registry.template(template_id)
    sections = list(original.sections)
    sections[0] = sections[0].copy()
    sections[0].says = new_says
    bumped = replace(original, version=version, sections=tuple(sections))

    patched = dict(registry.templates())
    patched[template_id] = bumped
    monkeypatch.setattr(registry, "templates", lambda: patched)
    monkeypatch.setattr(registry, "template", lambda tid: patched[tid])
    monkeypatch.setattr(registry, "current_version",
                        lambda tid: patched[tid].version if tid in patched else None)
