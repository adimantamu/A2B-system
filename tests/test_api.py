"""End-to-end through the HTTP surface, including the MCP tool calls.

This walks the fifteen-minute loop the brief describes: paste the URL, confirm,
open a skill, add a Library section, set the destination, publish, then look at
the agent page — and then runs the skill the way a customer's AI would.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from a2b import sessions as sessions_mod
from a2b import skills as skills_mod
from a2b.api.main import app
from a2b.compiler import (PDF_OFFER, SECTION_CHECK, SUMMARY_OFFER,
                          WORK_FROM_THE_PAGE)


def skills_for(_client, vendor_slug: str, skill_slug: str) -> dict:
    """The skill as stored, to compare against what the public pages render."""
    return skills_mod.get_skill_by_slug(vendor_slug, skill_slug)


BANK_HTML = """
<html><head><title>Bluebonnet Community Bank</title></head><body>
  <h1>Lending, decided locally.</h1><p>Member FDIC, serving the Hill Country since 1949.</p>
  <h2>Apartment &amp; multifamily lending</h2>
  <p>Financing for 5+ unit properties across the Hill Country.</p>
  <h2>Commercial real estate loans</h2><p>Own the building your business calls home.</p>
</body></html>
"""


@pytest.fixture
def client():
    """Anonymous. Use this for the public doors and the MCP surface."""
    return TestClient(app)


@pytest.fixture
def owner(client):
    """Signed in. TestClient keeps the session cookie, so every later call on
    this client is authenticated as this user."""
    client.post("/api/auth/signup", json={
        "email": "owner@bluebonnetbank.com", "password": "correct-horse-staple",
        "name": "Sarah Whitley",
    }).raise_for_status()
    return client


def test_health_reports_the_loaded_registry(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["templates"] > 0 and body["products"] > 0


def _walk_vendor_journey(client) -> tuple[str, str]:
    """Build, configure and publish a skill. Returns (vendor_id, vendor_slug)."""
    # 1. Vendor exists.
    vendor = client.post("/api/vendors", json={
        "name": "Bluebonnet Community Bank", "industry_key": "banking",
        "website": "bluebonnetbank.com", "description": "Community bank in Boerne, TX.",
    }).json()["vendor"]
    vid = vendor["id"]

    # 2. Scan the site. Suggestions only — nothing is bound.
    scan = client.post("/api/ingest/scan", json={
        "url": "https://bluebonnetbank.com", "vendor_id": vid, "html": BANK_HTML,
    }).json()
    assert scan["industry_suggestions"][0]["key"] == "banking"
    assert all(m["confirmed"] == 0 for m in scan["matches"])
    assert client.get(f"/api/vendors/{vid}").json()["skills"] == []

    # 3. Confirm one offering. It becomes a draft skill.
    chosen = next(m for m in scan["matches"] if m["product_key"] == "uw-multifamily-perm")
    confirmed = client.post(f"/api/ingest/{scan['run_id']}/confirm", json={
        "vendor_id": vid, "match_ids": [chosen["id"]], "industry_key": "banking",
    }).json()
    skill_id = confirmed["created_skill_ids"][0]

    detail = client.get(f"/api/skills/{skill_id}").json()
    assert detail["skill"]["status"] == "draft"
    assert detail["skill"]["template_status"]["pinned_version"] == "1.0"
    assert detail["can_publish"] is False  # no destination yet

    # 4. Add a Library section. It lands at the end, so it belongs to the intake
    #    without belonging to the opening message — the entry prompt carries only
    #    section 1. The copy-everything prompt is where the whole thing shows up.
    client.post(f"/api/skills/{skill_id}/sections/library",
                json={"industry_key": "banking", "index": 0})
    assert "Insurance quote" in client.get(f"/api/skills/{skill_id}").json()["links"]["full_prompt"]

    # 5. Core sections are protected; added ones are not.
    skill = client.get(f"/api/skills/{skill_id}").json()["skill"]
    core = next(s for s in skill["sections"] if s["is_core"])
    added = next(s for s in skill["sections"] if not s["is_core"])
    assert client.delete(f"/api/sections/{core['id']}").status_code == 409
    assert client.delete(f"/api/sections/{added['id']}").status_code == 200

    # 6. Publishing without a destination is refused.
    assert client.post(f"/api/skills/{skill_id}/publish").status_code == 400

    # 7. Set the destination, then publish.
    client.post(f"/api/skills/{skill_id}/connect-convey",
                params={"url": "https://bluebonnet.convey.lenni.io"})
    assert client.post(f"/api/skills/{skill_id}/publish").status_code == 200

    # 8. Both publish outputs exist and agree.
    slug = vendor["slug"]
    txt = client.get(f"/v/{slug}/llms.txt").text
    md = client.get(f"/v/{slug}/llms.md").text
    assert "Multifamily Permanent" in txt and "Multifamily Permanent" in md
    assert "not a credit decision" in md

    start = client.get(f"/v/{slug}/start/multifamily-perm")
    assert start.status_code == 200
    assert "Multifamily Permanent" in start.text

    packet = client.get(f"/v/{slug}/webmaster").json()
    assert 'rel="llms"' in packet["head"]

    return vid, slug


def test_full_vendor_journey(owner):
    _walk_vendor_journey(owner)


def test_customer_runs_the_skill_over_mcp(owner, client):
    _, slug = _walk_vendor_journey(owner)

    tools = {t["name"] for t in client.get("/mcp/tools").json()["tools"]}
    assert {"lenni_start_session", "lenni_complete_section", "lenni_finish"} <= tools

    def call(name, arguments, **kw):
        return client.post("/mcp/call", json={"name": name, "arguments": arguments, **kw}).json()

    started = call("lenni_start_session", {
        "vendor": slug, "skill": "multifamily-perm", "email": "borrower@example.com",
        "terminal": "chatgpt",
    })["result"]
    sid = started["session_id"]
    assert started["section"]["name"] == "Collateral"
    assert started["section"]["of"] == 3
    assert "rate" in started["guardrail"]

    # Chain through every section.
    section = started["section"]
    while True:
        step = call("lenni_complete_section", {
            "session_id": sid, "section_id": section["section_id"],
            "facts": {f"{section['name'].lower()}_note": "captured"},
        })["result"]
        if step["done"]:
            break
        section = step["section"]

    assert call("lenni_validate_package", {"session_id": sid})["result"]["complete"] is True

    finished = call("lenni_finish", {"session_id": sid})["result"]
    assert finished["validated"] is True
    assert finished["outputs"]["markdown"]
    assert "Convey" in finished["destination"]

    # The profile is no longer reachable by email — see #24 and the block of
    # comment where the tool used to be. The run above is unaffected by that,
    # which is the point of asserting it here rather than in isolation.
    gone = call("lenni_get_user_context", {"email": "borrower@example.com"})
    assert "result" not in gone
    assert gone["error"]["code"] == "unknown_tool"


def test_the_workflow_is_readable_as_markdown(owner, client):
    """The address the entry prompt tells the AI to read. It 404'd for months,
    because the prompt pointed at `/mcp?...` and nothing was registered there."""
    _, slug = _walk_vendor_journey(owner)

    page = client.get(f"/v/{slug}/start/multifamily-perm.md")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/plain")

    body = page.text
    assert "Multifamily Permanent" in body
    assert "not a credit decision" in body
    # The questions themselves, not just the section headings.
    skill = skills_for(client, slug, "multifamily-perm")
    for section in skill["sections"]:
        assert section["name"] in body
        for question in section["asks"]:
            assert question in body


def test_each_section_is_served_on_its_own_page(owner, client):
    """The chain the entry prompt starts. Section 1 arrives in the prompt; every
    section after it has to be fetchable on its own, or the customer's AI finishes
    the first one and stops."""
    _, slug = _walk_vendor_journey(owner)
    sections = skills_for(client, slug, "multifamily-perm")["sections"]
    total = len(sections)

    for position, section in enumerate(sections, 1):
        page = client.get(f"/v/{slug}/start/multifamily-perm/{position}.md")
        assert page.status_code == 200, f"section {position} is not served"
        assert page.headers["content-type"].startswith("text/plain")
        body = page.text

        assert f"Section {position} of {total}: {section['name']}" in body
        for question in section["asks"]:
            assert question in body
        assert "not a credit decision" in body


def test_a_section_page_hands_over_the_next_one_and_stops_at_the_end(owner, client):
    """Each page names its successor; the last names none and closes instead. A
    page that pointed past the end sent the AI to a 404 mid-interview."""
    _, slug = _walk_vendor_journey(owner)
    total = len(skills_for(client, slug, "multifamily-perm")["sections"])

    for position in range(1, total):
        body = client.get(f"/v/{slug}/start/multifamily-perm/{position}.md").text
        assert f"/start/multifamily-perm/{position + 1}.md" in body

    last = client.get(f"/v/{slug}/start/multifamily-perm/{total}.md").text
    assert "last section" in last
    assert f"/start/multifamily-perm/{total + 1}.md" not in last
    # And it carries the closing message, so the run has a real ending.
    assert "your package is complete" in last
    assert "Convey" in last


def test_a_section_page_past_the_end_is_a_404(owner, client):
    _, slug = _walk_vendor_journey(owner)
    total = len(skills_for(client, slug, "multifamily-perm")["sections"])
    assert client.get(f"/v/{slug}/start/multifamily-perm/{total + 1}.md").status_code == 404
    assert client.get(f"/v/{slug}/start/multifamily-perm/0.md").status_code == 404


def test_a_section_page_and_the_whole_workflow_agree(owner, client):
    """Two renderings of the same section. If they drift, a customer who reads the
    index and one who follows the chain get different interviews."""
    _, slug = _walk_vendor_journey(owner)
    whole = client.get(f"/v/{slug}/start/multifamily-perm.md").text

    for position, section in enumerate(
            skills_for(client, slug, "multifamily-perm")["sections"], 1):
        page = client.get(f"/v/{slug}/start/multifamily-perm/{position}.md").text
        for question in section["asks"]:
            assert question in page and question in whole


def test_the_start_page_offers_the_whole_intake_for_assistants_that_cannot_browse(
        owner, client):
    """The floor under the chain. Browsing is off in some assistants and blocked on
    some networks; without this those customers get one section and a dead end."""
    _, slug = _walk_vendor_journey(owner)
    page = client.get(f"/v/{slug}/start/multifamily-perm").text

    for section in skills_for(client, slug, "multifamily-perm")["sections"]:
        for question in section["asks"]:
            assert question.replace("<", "&lt;") in page


def test_a_connector_only_start_page_does_not_publish_the_intake_anyway(owner, client):
    """The escape hatch above is a contradiction on a connector-only skill. The
    vendor's whole reason for turning it on is that their questions stay on their
    server, and a paste-the-whole-intake panel puts every one of them on a public
    page regardless of the setting."""
    _, slug = _walk_vendor_journey(owner)
    skill = skills_for(owner, slug, "multifamily-perm")
    owner.post(f"/api/skills/{skill['id']}/entry-mode", params={"mode": "connector"})

    page = client.get(f"/v/{slug}/start/multifamily-perm").text

    for section in skills_for(owner, slug, "multifamily-perm")["sections"]:
        for question in section["asks"]:
            assert question.replace("<", "&lt;") not in page, f"leaked: {question!r}"

    # And what replaces it has to be useful, since a customer reading it is stuck
    # until they add the connector.
    assert "Add custom connector" in page
    assert 'id="copyfull"' not in page


def test_the_product_docs_answer_at_the_address_the_prompt_will_name(owner, client):
    """These two URLs are what a handed-off assistant is told to fetch. If they
    404 the assistant does not report a broken link — it invents an intake and
    sounds sure about it, which is the failure the whole chain exists to stop."""
    _, slug = _walk_vendor_journey(owner)

    card = client.get(f"/v/{slug}/multifamily-perm/llms.txt")
    guide = client.get(f"/v/{slug}/multifamily-perm/context.md")
    assert card.status_code == 200 and guide.status_code == 200
    assert "Multifamily Permanent" in card.text
    assert "Multifamily Permanent" in guide.text

    # And they must not shadow the start routes that share their shape.
    assert client.get(f"/v/{slug}/start/multifamily-perm").status_code == 200
    assert client.get(f"/v/{slug}/start/multifamily-perm/1.md").status_code == 200


def test_every_page_that_can_end_an_intake_offers_the_pdf(owner, client):
    """An assistant that walked the section chain, or fell back to the one-page copy,
    finishes somewhere other than the guide. Whichever page it happens to be reading
    has to close the same way, or whether the customer is offered a document comes
    down to which link their terminal chose to follow."""
    _, slug = _walk_vendor_journey(owner)
    skill = skills_for(client, slug, "multifamily-perm")
    last = len(skill["sections"])

    for url in (f"/v/{slug}/multifamily-perm/context.md",
                f"/v/{slug}/start/multifamily-perm/{last}.md",
                f"/v/{slug}/start/multifamily-perm.md"):
        body = client.get(url).text
        assert PDF_OFFER in body, url
        # And the summary is offered first, wherever the intake ends.
        assert body.index(SUMMARY_OFFER) < body.index(PDF_OFFER), url


def test_every_page_says_to_ask_the_vendors_questions_not_its_own(owner, client):
    """Whichever page carries questions carries the instruction to use them. A page
    that shows the wording without insisting on it invites a fluent paraphrase, and
    a paraphrased underwriting question is a different question."""
    _, slug = _walk_vendor_journey(owner)

    for url in (f"/v/{slug}/multifamily-perm/context.md",
                f"/v/{slug}/start/multifamily-perm/1.md",
                f"/v/{slug}/start/multifamily-perm.md"):
        assert WORK_FROM_THE_PAGE in client.get(url).text, url


def test_every_page_hands_between_sections_the_same_way(owner, client):
    """The checkpoint has to survive the route the assistant actually took. A section
    page that advances on its own judgement, while the guide waits to be told, is the
    same intake behaving differently depending on which link was followed."""
    _, slug = _walk_vendor_journey(owner)
    skill = skills_for(client, slug, "multifamily-perm")
    last = len(skill["sections"])

    for url in (f"/v/{slug}/multifamily-perm/context.md",
                f"/v/{slug}/start/multifamily-perm/1.md",
                f"/v/{slug}/start/multifamily-perm.md"):
        assert SECTION_CHECK in client.get(url).text, url

    # Except at the end, where "next section" would be a lie — that one closes.
    tail = client.get(f"/v/{slug}/start/multifamily-perm/{last}.md").text
    assert SECTION_CHECK not in tail
    assert PDF_OFFER in tail


def test_the_last_section_does_not_lead_with_delivery(owner, client):
    """Delivery said before the file exists is what buried the ask in a live run."""
    _, slug = _walk_vendor_journey(owner)
    skill = skills_for(client, slug, "multifamily-perm")
    body = client.get(
        f"/v/{slug}/start/multifamily-perm/{len(skill['sections'])}.md").text

    assert body.index(PDF_OFFER) < body.index("Convey")


def test_reading_the_guide_is_recorded_where_the_vendor_can_see_it(owner, client):
    """"How do you know it read our questions?" is a fair question from a bank. The
    answer used to be a code the assistant recited, which took the word of the one
    party that might be making it up — and put it in the customer's chat. The server
    saw the request; that is the party worth asking."""
    vendor_id, slug = _walk_vendor_journey(owner)

    def reads() -> int:
        return sessions_mod.funnel_summary(vendor_id)["events"].get("guide_read", 0)

    before = reads()
    client.get(f"/v/{slug}/multifamily-perm/context.md")
    assert reads() == before + 1


def test_a_fetcher_that_checks_permission_first_is_told_yes(client):
    """Anthropic's fetcher asked for robots.txt, got a 404, and never went on to read
    the guide it had been pointed at. A missing file is ambiguous rather than
    permissive, and these pages exist to be fetched."""
    r = client.get("/robots.txt")

    assert r.status_code == 200
    assert "Allow: /v/" in r.text
    # The vendor's own workspace is not content and should not be crawled.
    assert "Disallow: /console/" in r.text


def test_agent_docs_are_never_served_from_a_cache(owner, client):
    """A cached copy breaks the promise the hosted docs exist to keep — the vendor
    edits a question and an intermediary hands the next customer yesterday's. It also
    destroys the read log as evidence, because a cache hit never reaches us."""
    _, slug = _walk_vendor_journey(owner)

    for url in (f"/v/{slug}/multifamily-perm/context.md",
                f"/v/{slug}/multifamily-perm/llms.txt",
                f"/v/{slug}/start/multifamily-perm.md",
                f"/v/{slug}/start/multifamily-perm/1.md",
                f"/v/{slug}/llms.md"):
        assert "no-store" in client.get(url).headers.get("cache-control", ""), url


def test_product_docs_404_for_a_draft_rather_than_describing_it(owner, client):
    _, slug = _walk_vendor_journey(owner)
    assert client.get(f"/v/{slug}/cre-owner-occupied/context.md").status_code == 404


def test_the_start_page_hands_the_in_page_agent_layer_what_it_needs(owner, client):
    """The panel is built in the browser from /mcp/tools, so the page's whole job
    is to load the script and say which vendor and skill it is looking at. Getting
    that config wrong fails silently — an empty box and no error."""
    _, slug = _walk_vendor_journey(owner)
    page = client.get(f"/v/{slug}/start/multifamily-perm").text

    assert 'id="a2b-webmcp"' in page
    assert "/static/webmcp.js" in page
    assert f'"vendor": "{slug}"' in page
    assert '"skill": "multifamily-perm"' in page


def test_the_in_page_tool_script_is_actually_served(client):
    """Mounted from a directory, so a rename or a missed deploy step breaks it
    without touching any Python the tests cover."""
    out = client.get("/static/webmcp.js")
    assert out.status_code == 200
    # The two API spellings, and the proxy that makes the tools real rather than
    # a page-local imitation.
    assert "modelContext" in out.text
    assert "/mcp/call" in out.text


def test_tools_called_from_the_page_are_the_tools_the_connector_exposes(client):
    """The page must not grow its own catalogue. If these ever diverge, a demo
    stops predicting what a customer's assistant will do, which is the only
    reason the panel is worth showing."""
    listed = {t["name"] for t in client.get("/mcp/tools").json()["tools"]}
    assert "lenni_start_session" in listed

    out = client.post("/mcp/call", json={"name": "lenni_list_services",
                                         "arguments": {}, "vendor": "nope"})
    # Reached the real tool: it answers, or refuses for a real reason.
    assert out.status_code == 200
    assert "result" in out.json() or "error" in out.json()


def test_the_mcp_root_explains_itself_instead_of_404ing(client):
    """An assistant that followed the old entry prompt got a bare 404 and
    concluded the vendor was offline. It now serves a real connector, so the
    answer names the transport and still points at the workflow that was asked
    for — while saying the one thing an assistant reading this cannot do, which
    is attach itself from a URL it found in a prompt."""
    out = client.get("/mcp", params={"vendor": "some-bank", "skill": "ci-general"})
    assert out.status_code == 200
    body = out.json()
    assert body["transport"] == "streamable-http"
    assert "custom connector" in body["description"].lower()
    assert body["workflow"].endswith("/v/some-bank/start/ci-general.md")


def test_draft_skills_are_unreachable_over_mcp(owner):
    client = owner
    vendor = client.post("/api/vendors", json={
        "name": "Draft Bank", "industry_key": "banking", "website": "draftbank.com",
    }).json()["vendor"]
    skill_id = client.post(f"/api/vendors/{vendor['id']}/skills/seed",
                           params={"template_id": "uw-multifamily-perm"}).json()["skill"]["id"]

    out = client.post("/mcp/call", json={
        "name": "lenni_start_session",
        "arguments": {"vendor": vendor["slug"], "skill": "multifamily-perm"},
    }).json()
    assert out["error"]["code"] == "not_published"

    assert client.get(f"/v/{vendor['slug']}/start/multifamily-perm").status_code == 404
    assert client.get(f"/v/{vendor['slug']}/start/multifamily-perm.md").status_code == 404

    # And the console is told, so it can stop presenting the links as live. One
    # of these went to a customer's Claude and dead-ended.
    assert client.get(f"/api/skills/{skill_id}").json()["links"]["live"] is False


def test_publishing_makes_the_start_links_live(owner):
    _, slug = _walk_vendor_journey(owner)
    skill = skills_for(owner, slug, "multifamily-perm")
    assert owner.get(f"/api/skills/{skill['id']}").json()["links"]["live"] is True


def test_marketplace_matching_is_off_inside_a_vendor_session(client):
    out = client.post("/mcp/call", json={
        "name": "lenni_match_vendors", "arguments": {"product_key": "uw-multifamily-perm"},
        "vendor": "bluebonnet-community-bank",
    }).json()
    assert out["error"]["code"] == "disabled_on_vendor_site"


def test_unknown_tool_lists_what_is_available(client):
    out = client.post("/mcp/call", json={"name": "nope", "arguments": {}}).json()
    assert out["error"]["code"] == "unknown_tool"
    assert "lenni_start_session" in out["error"]["hint"]
