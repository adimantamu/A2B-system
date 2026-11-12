"""The library is a showroom: no account, and the questions themselves."""
from __future__ import annotations

from fastapi.testclient import TestClient

from a2b.api.main import app


def test_templates_are_public_and_carry_the_questions():
    client = TestClient(app)
    body = client.get("/api/registry/templates").json()
    assert body["templates"], "registry returned no templates"
    banking = [t for t in body["templates"] if t["industry"] == "banking"]
    assert banking, "banking has no templates to show"
    tailored = next(t for t in banking if t["tailored"])
    assert tailored["sections"], f"{tailored['id']} has no sections"
    first = tailored["sections"][0]
    assert first["name"] and (first["asks"] or first["says"])


def test_library_endpoint_includes_the_opening_line():
    client = TestClient(app)
    lib = client.get("/api/registry/library").json()["library"]
    assert "banking" in lib
    quoted = next(s for s in lib["banking"] if s.get("says"))
    assert quoted["asks"]


def test_industry_filter_does_not_leak_other_industries():
    client = TestClient(app)
    body = client.get("/api/registry/templates", params={"industry": "legal"}).json()
    assert body["templates"]
    assert all(t["industry"] == "legal" for t in body["templates"])


def test_every_seedable_industry_has_something_to_read():
    """The library must never answer an industry card with a blank page.

    Templates are synthesised per product, so an industry whose services are not
    catalogued yet resolves to zero of them. Its core intake is what the page
    shows instead, and `templates: true` is the promise that one exists.
    """
    client = TestClient(app)
    industries = client.get("/api/registry/industries").json()["industries"]
    templates = client.get("/api/registry/templates").json()["templates"]
    core = client.get("/api/registry/core").json()["core"]

    for ind in [i for i in industries if i["templates"]]:
        key = ind["key"]
        has_templates = any(t["industry"] == key for t in templates)
        has_core = bool(core.get(key))
        assert has_templates or has_core, f"{key} shows an empty library"
        assert has_core, f"{key} is seedable but has no core intake"


def test_core_intake_carries_the_questions():
    client = TestClient(app)
    core = client.get("/api/registry/core", params={"industry": "msp"}).json()["core"]
    assert list(core) == ["msp"]
    first = core["msp"][0]
    assert first["name"] and first["asks"]
