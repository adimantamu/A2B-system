"""Website ingest: suggest, never bind."""
from __future__ import annotations

from a2b import ingest, skills

BANK_HTML = """
<html><head><title>Bluebonnet Community Bank — Lending, decided locally</title></head>
<body>
  <h1>Lending, decided locally.</h1>
  <p>Member FDIC. Serving the Texas Hill Country since 1949.</p>
  <h2>Apartment &amp; multifamily lending</h2>
  <p>Financing for 5+ unit properties across the Hill Country.</p>
  <h2>Commercial real estate loans</h2>
  <p>Own the building your business calls home.</p>
  <h2>Trust services</h2>
  <p>Serving Hill Country families for generations.</p>
</body></html>
"""


def test_scan_extracts_offerings_from_headings():
    scan = ingest.scan_html("https://bluebonnetbank.com", BANK_HTML)
    offerings = scan.offerings
    assert "Apartment & multifamily lending" in offerings
    assert "Commercial real estate loans" in offerings


def test_industry_is_suggested_from_site_signals():
    scan = ingest.scan_html("https://bluebonnetbank.com", BANK_HTML)
    ranked = ingest.detect_industry(scan)
    assert ranked and ranked[0][0] == "banking"


def test_high_confidence_matches_land_on_the_right_products():
    scan = ingest.scan_html("https://bluebonnetbank.com", BANK_HTML)
    matches = {m.found_label: m for m in ingest.match_scan(scan, "banking")}

    mf = matches["Apartment & multifamily lending"]
    assert mf.product_key == "uw-multifamily-perm"
    assert mf.confidence == ingest.HIGH

    cre = matches["Commercial real estate loans"]
    assert cre.product_key == "uw-cre-owner-occupied"
    assert cre.confidence == ingest.HIGH


def test_a_weak_tagline_does_not_steal_a_product_from_a_real_heading():
    """The h1 brushes against "lending" and scores just enough to register. It
    must not claim the multifamily product ahead of the heading that actually
    names it."""
    scan = ingest.scan_html("https://bluebonnetbank.com", BANK_HTML)
    matches = ingest.match_scan(scan, "banking")
    owner = next(m for m in matches if m.product_key == "uw-multifamily-perm")
    assert owner.found_label == "Apartment & multifamily lending"


def test_a_match_explains_itself_with_the_phrase_it_hit():
    """The vendor is confirming a suggestion, so the suggestion has to be
    inspectable — which registry phrase matched, and their own words beside it."""
    scan = ingest.scan_html("https://bluebonnetbank.com", BANK_HTML)
    mf = next(m for m in ingest.match_scan(scan, "banking")
              if m.product_key == "uw-multifamily-perm")
    assert mf.matched_phrase
    assert "5+ unit properties" in mf.found_quote


def test_nav_furniture_is_not_an_offering():
    """Measured on five real Texas bank sites. "Home" was bound to a residential
    mortgage and "Lending" to a multifamily loan, both at high confidence and
    both nav buttons, because a short label that sits inside a longer registry
    phrase used to score a decisive 1.0."""
    html = """<html><body>
      <nav><a href="/">Home</a><a href="/lending">Lending</a><a href="/business">Business</a></nav>
      <h2>Commercial real estate loans</h2><p>Own the building.</p>
    </body></html>"""
    scan = ingest.scan_html("https://example.com", html)
    labels = {m.found_label for m in ingest.match_scan(scan, "banking") if m.product_key}

    assert "Commercial real estate loans" in labels
    for furniture in ("Home", "Lending", "Business"):
        assert furniture not in labels


def test_a_short_label_that_is_genuinely_a_product_still_matches():
    """The other half of the furniture rule. Dropping every short label would
    take real offerings with it, so the test is on the name rather than the
    length."""
    m = ingest.match_offering("HELOC", "banking")
    assert m.product_key and m.confidence == ingest.HIGH


def test_prose_only_matches_a_phrase_it_actually_contains():
    """A sentence has enough words to brush against something. Both of these
    were scored as trust products on live bank sites — the first is the bank's
    own name, and the second is our registry's tagline meeting theirs."""
    for sentence in ("Credit card options at Security State Bank & Trust",
                     "Serving the great state of Texas for four generations"):
        assert ingest.match_offering(sentence, "banking").product_key is None, sentence

    # Containment is still decisive, so a wordy heading that really does name
    # the product keeps working.
    wordy = ingest.match_offering(
        "A smarter way to fund your next step with tailored SBA loans", "banking")
    assert wordy.product_key


def test_a_quote_never_borrows_the_next_services_description():
    """A short blurb must not cause the picker to run past the next heading and
    attribute a neighbouring service's copy to this one."""
    html = """<html><body>
      <h2>Auto loans</h2><p>New, used, refi.</p>
      <h2>Trust services</h2><p>Serving Hill Country families for generations.</p>
    </body></html>"""
    scan = ingest.scan_html("https://example.com", html)
    quotes = {m.found_label: m.found_quote for m in ingest.match_scan(scan, "banking")}
    assert quotes["Auto loans"] == "New, used, refi."
    assert "generations" not in quotes["Auto loans"]


def test_a_heading_with_no_description_gets_no_quote():
    html = "<html><body><h2>Auto loans</h2><h2>Trust services</h2></body></html>"
    scan = ingest.scan_html("https://example.com", html)
    quotes = {m.found_label: m.found_quote for m in ingest.match_scan(scan, "banking")}
    assert quotes["Auto loans"] == ""


def test_a_catalogued_offering_with_no_tailored_template_runs_the_generic_intake(vendor_id):
    """Trust Services is catalogued but nobody has written it a template.

    It used to become an empty custom skill, leaving the vendor to author the
    whole intake themselves. Now it seeds its industry's generic intake, so the
    thing they get on day one already asks the banking questions in order.
    """
    scan = ingest.scan_html("https://bluebonnetbank.com", BANK_HTML)
    matches = ingest.match_scan(scan, "banking")
    run_id = ingest.save_run(vendor_id, scan, "banking", matches)
    run = ingest.get_run(run_id)

    trust = next(m for m in run["matches"] if "Trust" in m["found_label"])
    assert trust["template_id"] == "trust-services"

    ingest.confirm_matches(run_id, vendor_id, [trust["id"]])
    created = skills.list_skills(vendor_id)
    assert len(created) == 1
    assert created[0]["is_custom"] is False
    assert created[0]["sections"], "the generic intake arrived with no questions"


def test_a_generic_intake_says_it_is_generic(vendor_id):
    """The vendor should be able to tell tailored from not-yet-tailored."""
    from a2b import registry

    assert registry.products()["trust-services"].bespoke is False
    assert registry.products()["uw-sba"].bespoke is True
    assert registry.template("trust-services").version.startswith("generic-")


def test_nothing_is_bound_until_a_human_confirms(vendor_id):
    scan = ingest.scan_html("https://bluebonnetbank.com", BANK_HTML)
    matches = ingest.match_scan(scan, "banking")
    run_id = ingest.save_run(vendor_id, scan, "banking", matches)

    assert all(m["confirmed"] == 0 for m in ingest.get_run(run_id)["matches"])
    assert skills.list_skills(vendor_id) == []


def test_confirming_a_subset_creates_only_those_skills(vendor_id):
    scan = ingest.scan_html("https://bluebonnetbank.com", BANK_HTML)
    matches = ingest.match_scan(scan, "banking")
    run_id = ingest.save_run(vendor_id, scan, "banking", matches)
    run = ingest.get_run(run_id)

    chosen = next(m for m in run["matches"] if m["product_key"] == "uw-multifamily-perm")
    ingest.confirm_matches(run_id, vendor_id, [chosen["id"]])

    created = skills.list_skills(vendor_id)
    assert len(created) == 1
    assert created[0]["name"] == "Multifamily Permanent"
    assert created[0]["status"] == "draft"  # confirmed is not published


def test_confirming_twice_does_not_duplicate_the_skill(vendor_id):
    scan = ingest.scan_html("https://bluebonnetbank.com", BANK_HTML)
    matches = ingest.match_scan(scan, "banking")
    run_id = ingest.save_run(vendor_id, scan, "banking", matches)
    chosen = next(m for m in ingest.get_run(run_id)["matches"]
                  if m["product_key"] == "uw-multifamily-perm")

    ingest.confirm_matches(run_id, vendor_id, [chosen["id"]])
    ingest.confirm_matches(run_id, vendor_id, [chosen["id"]])
    assert len(skills.list_skills(vendor_id)) == 1


def test_unmatched_offerings_aggregate_into_the_template_roadmap(vendor_id):
    html = """<html><body>
      <h2>Marine and RV lending</h2><p>Boats and campers, financed locally.</p>
      <h2>Aircraft financing</h2><p>Hangar to runway.</p>
    </body></html>"""
    scan = ingest.scan_html("https://example.com", html)
    ingest.save_run(vendor_id, scan, "banking", ingest.match_scan(scan, "banking"))

    labels = [d["label"] for d in ingest.unmatched_demand()]
    assert "Aircraft financing" in labels
