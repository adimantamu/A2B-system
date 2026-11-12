"""Live start links.

The promise under test is the one a bank's page depends on: the address pasted
onto the page never changes, and a click on it carries whatever prompt was
published at that moment. The rest is about who may change that prompt, because
a lenni.io link with a bank's name on it is worth abusing.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from a2b import live
from a2b.api.main import app
from a2b.config import LIVE_BASE
from a2b.db import cursor
from a2b.links import MAX_QUERY

STAFF = "adi@lenni.io"
PASSWORD = "correct-horse-staple"
BANK = "bluebonnet-community-bank"
LOAN = "auto-loan"
LIVE = f"/api/live/{BANK}/{LOAN}"
GUARDRAIL_LINE = ("This is preparation only: not a credit decision, not a rate quote, "
                  "and not a commitment to lend. The vendor makes all decisions.")


def body(prompt: str = "Help me prepare an auto loan application.", **overrides) -> dict:
    out = {"status": "published", "vendor_name": "Bluebonnet Community Bank",
           "product_name": "Auto Loan", "prompt": prompt}
    out.update(overrides)
    return out


def sign_up(email: str) -> TestClient:
    client = TestClient(app)
    client.post("/api/auth/signup",
                json={"email": email, "password": PASSWORD}).raise_for_status()
    return client


def click(client: TestClient, terminal: str = "claude"):
    return client.get(f"/go/{BANK}/{LOAN}/{terminal}", follow_redirects=False)


def prompt_in(location: str) -> str:
    return parse_qs(urlparse(location).query)["q"][0]


def intake(sections: int, asks: int = 3, opener: str = "", collect: str = "",
           tail: str = "") -> str:
    """A compiled entry prompt in the shape the workspace produces."""
    lines = ["I'd like help getting ready for Auto Loan.", "",
             "You are helping the customer complete an intake for this business using Lenni.",
             "", "--- Bluebonnet Community Bank: Auto Loan — the complete intake ---"]
    for n in range(1, sections + 1):
        lines += [f"Section {n} of {sections}: Part {n}",
                  f"  Open with: Now part {n}.{opener}", "  Ask:"]
        lines += [f"  - Question {n}.{a}: what about the trade-in?" for a in range(1, asks + 1)]
        lines += [f"  Collect: answers for part {n}{collect}", ""]
    lines += ["--- end of Bluebonnet Community Bank's intake ---", "",
              "Ask me the sections step by step, one at a time." + tail, "", GUARDRAIL_LINE]
    return "\n".join(lines)


@pytest.fixture
def staff(monkeypatch) -> TestClient:
    monkeypatch.setenv("A2B_STAFF_EMAILS", STAFF)
    monkeypatch.setenv("A2B_LIVE_VENDORS", BANK)
    return sign_up(STAFF)


# --------------------------------------------------------------------------
# The promise
# --------------------------------------------------------------------------

def test_an_edit_reaches_the_next_click_without_changing_the_link(staff):
    staff.put(LIVE, json=body("First wording.")).raise_for_status()
    first = click(staff)
    assert first.status_code == 302
    assert first.headers["location"].startswith("https://claude.ai/new?q=")
    assert prompt_in(first.headers["location"]) == "First wording."

    staff.put(LIVE, json=body("Second wording.")).raise_for_status()
    second = click(staff)
    assert prompt_in(second.headers["location"]) == "Second wording."


def test_a_redirect_is_never_cached(staff):
    # A cached redirect would keep sending customers to yesterday's prompt.
    staff.put(LIVE, json=body()).raise_for_status()
    assert click(staff).headers["cache-control"] == "no-store"


def test_each_assistant_gets_its_own_address(staff):
    staff.put(LIVE, json=body("Same prompt.")).raise_for_status()
    assert click(staff, "chatgpt").headers["location"].startswith("https://chatgpt.com/?q=")
    assert click(staff, "grok").headers["location"].startswith("https://grok.com/?q=")


def test_publishing_returns_the_fixed_links_to_paste(staff):
    links = staff.put(LIVE, json=body()).json()["live"]["links"]
    assert links["terminals"]["claude"] == f"{LIVE_BASE}/go/{BANK}/{LOAN}/claude"
    assert links["page"] == f"{LIVE_BASE}/go/{BANK}/{LOAN}"
    # No terminal that cannot take a prompt is offered as a link.
    assert "copilot" not in links["terminals"]


def test_a_click_is_recorded_with_the_version_it_served(staff):
    staff.put(LIVE, json=body("v1")).raise_for_status()
    staff.put(LIVE, json=body("v2")).raise_for_status()
    click(staff, "chatgpt")
    with cursor() as cur:
        rows = cur.execute(
            "SELECT detail_json FROM funnel_event WHERE event='live_link_opened'").fetchall()
    assert len(rows) == 1
    assert '"version": 2' in rows[0]["detail_json"]
    assert '"terminal": "chatgpt"' in rows[0]["detail_json"]


# --------------------------------------------------------------------------
# When there is nothing to open
# --------------------------------------------------------------------------

def test_copilot_lands_on_the_page_with_the_prompt_to_copy(staff):
    staff.put(LIVE, json=body("Copy me.")).raise_for_status()
    r = click(staff, "copilot")
    assert r.status_code == 302 and r.headers["location"] == f"/go/{BANK}/{LOAN}"
    page = staff.get(f"/go/{BANK}/{LOAN}")
    assert "Copy prompt for Copilot" in page.text and "Copy me." in page.text


def test_an_unpublished_product_shows_its_notice_and_hands_out_nothing(staff):
    staff.put(LIVE, json=body("Secret wording.")).raise_for_status()
    staff.put(LIVE, json=body("", status="unpublished",
                              notice="Auto loans reopen in March.")).raise_for_status()

    r = click(staff)
    assert r.status_code == 302 and r.headers["location"] == f"/go/{BANK}/{LOAN}"
    page = staff.get(f"/go/{BANK}/{LOAN}")
    assert "Auto loans reopen in March." in page.text
    assert "Secret wording." not in page.text
    assert staff.get(f"/go/{BANK}/{LOAN}.md").status_code == 404
    assert TestClient(app).get(LIVE).json()["prompt"] == ""


def test_an_unknown_service_or_assistant_says_so_in_a_page(staff):
    staff.put(LIVE, json=body()).raise_for_status()
    for path in (f"/go/{BANK}/no-such-loan/claude", f"/go/{BANK}/{LOAN}/bard",
                 f"/go/{BANK}/no-such-loan", "/go/Not_A_Slug!/x/claude"):
        r = staff.get(path, follow_redirects=False)
        assert r.status_code == 404, path
        assert "isn't active" in r.text, path


# --------------------------------------------------------------------------
# A prompt longer than a link
# --------------------------------------------------------------------------
#
# The click has to hand the assistant what the workspace's own buttons hand it,
# so these follow the order packForUrl sheds things in.

def clicked_prompt(staff: TestClient, prompt: str) -> str:
    staff.put(LIVE, json=body(prompt)).raise_for_status()
    location = click(staff).headers["location"]
    assert len(location.split("?q=", 1)[1]) <= MAX_QUERY
    return prompt_in(location)


def test_a_long_intake_drops_the_restatements_before_any_question(staff):
    prompt = intake(4, collect=" — restated for the assistant" * 45)
    assert len(live.encode_component(prompt)) > MAX_QUERY
    sent = clicked_prompt(staff, prompt)
    assert "Collect:" not in sent
    assert sent.count("Open with:") == 4
    assert all(f"Question {n}.{a}:" in sent for n in range(1, 5) for a in range(1, 4))
    assert sent.endswith(GUARDRAIL_LINE)


def test_a_longer_intake_drops_the_openers_too(staff):
    prompt = intake(4, opener=" Some colour for the customer." * 40,
                    collect=" — restated" * 40)
    sent = clicked_prompt(staff, prompt)
    assert "Collect:" not in sent and "Open with:" not in sent
    assert all(f"Question {n}.{a}:" in sent for n in range(1, 5) for a in range(1, 4))
    assert sent.endswith(GUARDRAIL_LINE)


def test_an_intake_too_long_even_then_loses_whole_sections_from_the_end(staff):
    prompt = intake(40, asks=6)
    sent = clicked_prompt(staff, prompt)
    assert "Section 1 of 40" in sent and "Section 40 of 40" not in sent
    assert "\n(Intake continues — ask me for the rest.)\n--- end of " in sent
    # Everything after the end marker travels, so the guardrail is never cut.
    assert sent.endswith(GUARDRAIL_LINE)
    # The full text is still there for an assistant that asks for the rest.
    assert prompt in staff.get(f"/go/{BANK}/{LOAN}.md").text


def test_a_prompt_too_long_for_any_link_points_the_assistant_at_the_text(staff):
    # Packing never cuts after the end marker, so a long enough closing message
    # cannot fit however much of the intake goes.
    prompt = intake(2, tail=" Keep a running record." * 900)
    assert len(live.encode_component(live.pack_for_url(prompt))) > live.MAX_REDIRECT_QUERY
    staff.put(LIVE, json=body(prompt)).raise_for_status()

    short = prompt_in(click(staff).headers["location"])
    assert f"{LIVE_BASE}/go/{BANK}/{LOAN}.md" in short
    assert len(short) < 500
    text = staff.get(f"/go/{BANK}/{LOAN}.md")
    assert text.status_code == 200 and prompt in text.text
    assert text.headers["cache-control"].startswith("no-store")


def test_packing_measures_the_way_the_browser_does():
    # encodeURIComponent leaves these alone; quoting them would make a prompt
    # that fits in the workspace look too long here.
    assert live.encode_component("Don't (ever) skip *this*! ~ok") == \
        "Don't%20(ever)%20skip%20*this*!%20~ok"
    assert live.pack_for_url("  \ufeffShort enough.\u3000 ") == "Short enough."


def test_the_last_resort_cut_counts_characters_the_way_the_browser_does():
    # With no end marker to cut at, the workspace keeps slice(0, 4000), which
    # counts an emoji as two.
    cut = live.pack_for_url("🚗" * 10 + "a" * 9000)
    assert cut == "🚗" * 10 + "a" * 3980
    # A pair the browser would split is dropped whole rather than halved.
    assert live.pack_for_url("a" * 3999 + "🚗" * 2000) == "a" * 3999


def test_a_line_that_only_mentions_collect_is_kept():
    prompt = intake(30, asks=6).replace("Part 1\n", "Part 1\n  Ask: Collect: the VIN\n", 1)
    assert "Ask: Collect: the VIN" in live.pack_for_url(prompt)


# --------------------------------------------------------------------------
# Who may change it
# --------------------------------------------------------------------------

def test_signed_out_cannot_publish():
    assert TestClient(app).put(LIVE, json=body()).status_code == 401


def test_a_stranger_cannot_put_a_prompt_behind_a_banks_name(monkeypatch):
    monkeypatch.setenv("A2B_STAFF_EMAILS", STAFF)
    stranger = sign_up("someone@example.com")
    assert stranger.put(LIVE, json=body("Send your SSN to me.")).status_code == 404
    assert live.get(BANK, LOAN) is None


def test_staff_publish_only_the_demo_organisations_they_were_given(staff):
    # A scanned prospect is a real bank's name. Editing it in the workspace must
    # not put a prompt behind a public lenni.io address.
    assert staff.put(LIVE, json=body()).status_code == 200
    r = staff.put("/api/live/frost-bank/consumer-auto", json=body())
    assert r.status_code == 403 and "not turned on" in r.json()["detail"]
    assert live.get("frost-bank", "consumer-auto") is None


def test_staff_may_publish_a_real_vendor_without_listing_it(staff, monkeypatch):
    monkeypatch.setenv("A2B_LIVE_VENDORS", "")
    owner = sign_up("owner@realbank.example")
    slug = owner.post("/api/vendors", json={"name": "Real Bank",
                                            "industry_key": "banking"}).json()["vendor"]["slug"]
    assert staff.put(f"/api/live/{slug}/consumer-auto", json=body()).status_code == 200
    assert staff.put(LIVE, json=body()).status_code == 403


def test_an_editor_of_the_real_bank_may_publish_and_a_viewer_may_not(monkeypatch):
    monkeypatch.setenv("A2B_STAFF_EMAILS", STAFF)
    admin = sign_up("owner@bluebonnetbank.com")
    vendor = admin.post("/api/vendors", json={"name": "Bluebonnet Community Bank",
                                              "industry_key": "banking"}).json()["vendor"]
    assert vendor["slug"] == BANK
    sign_up("editor@bluebonnetbank.com")
    viewer = sign_up("viewer@bluebonnetbank.com")
    admin.post(f"/api/vendors/{vendor['id']}/members",
               json={"email": "editor@bluebonnetbank.com", "role": "editor"}).raise_for_status()
    admin.post(f"/api/vendors/{vendor['id']}/members",
               json={"email": "viewer@bluebonnetbank.com", "role": "viewer"}).raise_for_status()

    editor = TestClient(app)
    editor.post("/api/auth/login", json={"email": "editor@bluebonnetbank.com",
                                         "password": PASSWORD}).raise_for_status()
    assert editor.put(LIVE, json=body()).status_code == 200
    assert viewer.put(LIVE, json=body("Viewer wording.")).status_code == 403
    # An editor of one bank is nobody at another.
    assert editor.put("/api/live/other-bank/auto-loan", json=body()).status_code == 404


def test_a_demo_organisation_is_staff_only_even_where_a_real_vendor_shares_its_slug(staff):
    # The seeded demo bank on the server has this slug and admins of its own.
    admin = sign_up("owner@bluebonnetbank.com")
    vendor = admin.post("/api/vendors", json={"name": "Bluebonnet Community Bank",
                                              "industry_key": "banking"}).json()["vendor"]
    assert vendor["slug"] == BANK
    r = admin.put(LIVE, json=body("Admin wording."))
    assert r.status_code == 403 and "staff" in r.json()["detail"]
    assert admin.get(f"{LIVE}/history").status_code == 403
    assert live.get(BANK, LOAN) is None
    assert staff.put(LIVE, json=body("Staff wording.")).status_code == 200


# --------------------------------------------------------------------------
# The change log
# --------------------------------------------------------------------------

def test_the_log_records_who_changed_what_and_in_which_order(staff):
    staff.put(LIVE, json=body("First.")).raise_for_status()
    staff.put(LIVE, json=body("Second.")).raise_for_status()
    log = staff.get(f"{LIVE}/history").json()["history"]
    assert [e["version"] for e in log] == [2, 1]
    assert [e["prompt"] for e in log] == ["Second.", "First."]
    assert {e["actor_email"] for e in log} == {STAFF}


def test_saving_the_same_thing_twice_is_one_version(staff):
    assert staff.put(LIVE, json=body("Same.")).json()["changed"] is True
    again = staff.put(LIVE, json=body("Same.")).json()
    assert again["changed"] is False and again["live"]["version"] == 1
    assert len(staff.get(f"{LIVE}/history").json()["history"]) == 1


def test_the_log_and_the_editor_are_not_public(staff):
    staff.put(LIVE, json=body()).raise_for_status()
    assert TestClient(app).get(f"{LIVE}/history").status_code == 401
    assert sign_up("nosy@example.com").get(f"{LIVE}/history").status_code == 404
    public = TestClient(app).get(LIVE).json()
    assert STAFF not in str(public)
    assert public["version"] == 1 and public["prompt"]


def test_an_organisation_lists_its_live_products_without_their_prompts(staff):
    staff.put(LIVE, json=body("Auto wording.")).raise_for_status()
    staff.put(f"/api/live/{BANK}/heloc", json=body("", status="unpublished")).raise_for_status()
    r = TestClient(app).get(f"/api/live/{BANK}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    products = r.json()["products"]
    assert [(p["slug"], p["status"]) for p in products] == [
        (LOAN, "published"), ("heloc", "unpublished")]
    assert products[0]["links"]["terminals"]["claude"] == f"{LIVE_BASE}/go/{BANK}/{LOAN}/claude"
    assert "Auto wording." not in r.text and STAFF not in r.text
    assert TestClient(app).get("/api/live/nobody-here").json()["products"] == []
    assert TestClient(app).get("/api/live/Not_A_Slug!").json()["products"] == []


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path, payload", [
    (LIVE, body(status="live")),
    (LIVE, body("")),
    (LIVE, body(vendor_name="")),
    (LIVE, body("x" * (live.MAX_PROMPT + 1))),
    (LIVE, body(page={"blob": "x" * live.MAX_PAGE})),
    (f"/api/live/{BANK}/Auto_Loan", body()),
])
def test_bad_input_is_refused(staff, path, payload):
    assert staff.put(path, json=payload).status_code == 400


def test_text_that_cannot_travel_in_a_link_is_refused_at_the_save(staff):
    # A lone surrogate survives JSON and fails UTF-8, which would otherwise
    # surface as a broken redirect at the click. Sent escaped, as a browser's
    # JSON.stringify sends it.
    r = staff.put(LIVE, content=json.dumps(body("Bad \ud800 text")),
                  headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422)
    assert live.get(BANK, LOAN) is None
    with pytest.raises(ValueError):
        live.put(BANK, LOAN, vendor_name="Bluebonnet Community Bank", product_name="Auto",
                 status="published", prompt="Bad \ud800 text", actor_email=STAFF)


def test_the_page_escapes_what_it_is_given(staff):
    staff.put(LIVE, json=body("</pre><script>alert(1)</script>",
                              product_name="<img src=x onerror=alert(1)>")).raise_for_status()
    page = staff.get(f"/go/{BANK}/{LOAN}").text
    assert "<script>alert(1)" not in page
    assert "<img src=x" not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page


def test_the_page_counts_sections_from_what_the_workspace_sent(staff):
    staff.put(LIVE, json=body(page={"product": {"sections": ["Vehicle", "Income", "Review"]}})
              ).raise_for_status()
    assert "walk you through 3 sections" in staff.get(f"/go/{BANK}/{LOAN}").text


# --------------------------------------------------------------------------
# Turning an organisation on
# --------------------------------------------------------------------------

def seed(tmp_path, monkeypatch, entries: list) -> int:
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "seed_live_links.py"
    spec = importlib.util.spec_from_file_location("seed_live_links", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payloads = tmp_path / "payloads.json"
    payloads.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["seed_live_links.py", str(payloads)])
    return module.main()


def test_seeding_writes_each_row_once_and_says_what_it_refused(tmp_path, monkeypatch, capsys):
    good = {"vendor": BANK, "product": LOAN, "body": body("Seeded wording.")}
    bad = {"vendor": BANK, "product": "trust-services", "body": body(status="maybe")}
    assert seed(tmp_path, monkeypatch, [good, bad]) == 1
    assert seed(tmp_path, monkeypatch, [good]) == 0
    out = capsys.readouterr().out
    assert f"written {BANK}/{LOAN}  v1  published" in out
    assert f"same     {BANK}/{LOAN}  v1  published" in out
    assert f"refused  {BANK}/trust-services" in out
    assert live.get(BANK, "trust-services") is None
    log = live.history(BANK, LOAN)
    assert [(e["version"], e["actor_email"]) for e in log] == [(1, "seed@lenni.io")]
    assert prompt_in(click(TestClient(app)).headers["location"]) == "Seeded wording."
