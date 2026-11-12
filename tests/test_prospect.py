"""The prospect scanner: read a real site, propose a product page, write nothing.

Offline throughout. The crawler is exercised through `monkeypatch`ed fetches
rather than the network, because a test that depends on a bank's marketing site
fails on the day they redesign it, and a test suite that needs the internet is a
test suite people stop running.
"""
from __future__ import annotations

import sys

import pytest

from a2b import ingest, prospect
from a2b.db import cursor

HOME = """
<html><head><title>Hill Country State Bank | Banking in the Texas Hill Country</title></head>
<body>
  <nav>
    <a href="/">Home</a>
    <a href="/login">Online Banking Login</a>
    <a href="/personal">Personal</a>
    <a href="/business/lending">Business Lending</a>
    <a href="/about/careers">Careers</a>
    <a href="/privacy">Privacy Notice</a>
  </nav>
  <h1>Lending, decided locally.</h1>
  <p>Member FDIC.</p>
</body></html>
"""

LENDING = """
<html><head><title>Business Lending | Hill Country State Bank</title></head>
<body>
  <h2>Commercial real estate loans</h2>
  <p>Own the building your business calls home.</p>
  <h2>Equipment loans</h2>
  <p>Trucks, machinery and fit-out, financed over their useful life.</p>
  <h2>Marine and RV lending</h2>
  <p>Boats and campers, financed locally.</p>
</body></html>
"""

PAGES = {
    "https://hillcountry.bank": HOME,
    "https://hillcountry.bank/business/lending": LENDING,
    "https://hillcountry.bank/personal": "<html><body><h2>Auto loans</h2></body></html>",
}


@pytest.fixture
def offline(monkeypatch):
    """Serve the fixture pages and fail loudly on anything else.

    Raising on an unexpected URL is the point: it is how the crawl's own choices
    — which links it follows and which it refuses — become testable.
    """
    requested: list[str] = []

    class _Resp:
        def __init__(self, text, url): self.text, self.headers, self.url = text, {"content-type": "text/html"}, url
        def raise_for_status(self): pass

    class _Session:
        headers: dict = {}
        def get(self, url, timeout=None):
            requested.append(url)
            if url not in PAGES:
                raise RuntimeError(f"unexpected fetch: {url}")
            return _Resp(PAGES[url], url)

    # `crawl` imports requests inside the function, so replacing the module in
    # sys.modules is enough and no network stack is involved at all.
    monkeypatch.setitem(sys.modules, "requests",
                        type("m", (), {"Session": staticmethod(lambda: _Session())}))
    return requested


def test_the_crawl_follows_product_pages_and_leaves_the_furniture(offline):
    """The budget is six pages. Spending one on the privacy notice is a page not
    spent on the lending page, which is where the products actually are."""
    pages = prospect.crawl("https://hillcountry.bank")
    urls = [p.url for p in pages]

    assert "https://hillcountry.bank/business/lending" in urls
    assert "https://hillcountry.bank/personal" in urls
    for junk in ("/login", "/about/careers", "/privacy"):
        assert not any(u.endswith(junk) for u in urls), junk


def test_a_product_named_only_on_an_inner_page_is_still_found(offline):
    """The reason for crawling at all. Commercial real estate is named on the
    lending page and nowhere on the homepage — a single-page scan reports the
    nav categories and misses every actual product."""
    out = prospect.preview("https://hillcountry.bank")
    keys = {p["product_key"] for p in out.products}
    assert "uw-cre-owner-occupied" in keys


def test_nav_furniture_never_becomes_a_product(offline):
    """Measured on five real bank sites: "Home" was matched to a residential
    mortgage and "Lending" to a multifamily loan, both at high confidence, and
    both are nav buttons. A third of what we would have shown a bank was site
    furniture wearing a product name."""
    out = prospect.preview("https://hillcountry.bank")
    for p in out.products:
        assert not ingest._is_furniture(p["found_label"]), p


def test_a_scan_writes_nothing(offline):
    """What separates this from the ingest path. Scanning five banks nobody has
    a relationship with must not leave five runs behind, and a preview that
    creates state cannot be run twice in front of an audience."""
    def counts():
        with cursor() as cur:
            return tuple(cur.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                         for t in ("ingest_run", "ingest_match", "vendor", "vendor_skill"))

    before = counts()
    prospect.preview("https://hillcountry.bank")
    assert counts() == before


def test_every_match_carries_the_evidence_for_itself(offline):
    """A vendor looking at this is being asked to believe we read their site.
    The label we found and their own sentence under it are what make that
    checkable rather than a claim."""
    out = prospect.preview("https://hillcountry.bank")
    cre = next(p for p in out.products if p["product_key"] == "uw-cre-owner-occupied")

    assert cre["found_label"] == "Commercial real estate loans"
    assert "calls home" in cre["found_quote"]
    assert cre["matched_phrase"]


def test_an_offering_with_no_template_is_reported_not_dropped(offline):
    """Marine and RV lending is a real service with no template. Silently
    dropping it tells the bank we did not see it; reporting it is what feeds the
    template roadmap."""
    out = prospect.preview("https://hillcountry.bank")
    assert any("Marine" in u for u in out.unmatched)


def test_the_unmatched_list_does_not_read_like_a_sitemap(offline):
    """Left unfiltered this fills with "Accessibility Statement" and "Audio
    Captcha", which makes the scan look unable to tell a service from a page
    footer."""
    out = prospect.preview("https://hillcountry.bank")
    assert not any(u.lower() in ("privacy notice", "careers") for u in out.unmatched)


def test_a_site_name_is_the_business_not_the_page_title(offline):
    out = prospect.preview("https://hillcountry.bank")
    assert out.name == "Hill Country State Bank"


@pytest.mark.parametrize("title,expected", [
    ("Security State Bank &amp; Trust", "Security State Bank & Trust"),
    ("Home Page", "example.com"),
    ("Legend Bank | Personal & Business Banking", "Legend Bank"),
])
def test_a_page_title_is_not_always_a_name(title, expected):
    """All three seen on real bank sites. An entity that survives into the name
    reads as our bug, and a bank called "Home Page" reads as no scan at all."""
    scan = ingest.scan_html("https://example.com", f"<html><head><title>{title}</title></head></html>")
    assert prospect._site_name(scan, "https://example.com") == expected


def test_the_scanner_endpoints_are_closed_to_strangers():
    """Both spend our outbound requests, or name real institutions, on a
    caller's say-so. The ingest scan is deliberately open because a prospective
    vendor scans their own site before they have an account; neither of these is
    that, and an open crawler pointed at a host of the caller's choosing is
    something we would be handing to whoever finds it."""
    from fastapi.testclient import TestClient

    from a2b.api.main import app

    client = TestClient(app)
    assert client.get("/api/prospect/shortlist").status_code == 401
    assert client.post("/api/prospect/preview",
                       json={"url": "https://example.com"}).status_code == 401


def test_the_report_is_their_catalog_not_our_registry(offline):
    """The complaint this answers: a bank with a full product line saw eight
    rows, because eight was how many of their products our registry knew. The
    catalog has to be everything they sell, with our template library recorded
    against it rather than deciding what appears."""
    out = prospect.preview("https://hillcountry.bank")

    labels = {o["label"] for o in out.offerings}
    assert "Business Lending" in labels
    assert len(out.offerings) > len(out.products)
    # Marine and RV lending has no template and is in the catalog regardless.
    assert any("Marine" in o["label"] for o in out.offerings)


def test_every_offering_says_whether_we_can_build_it(offline):
    """Two different questions — what do they sell, and what can we ship — and
    conflating them is what made the old report misleading."""
    out = prospect.preview("https://hillcountry.bank")
    for o in out.offerings:
        assert o["has_template"] == bool(o["template_id"])
        if o["has_template"]:
            assert o["product_name"] and o["confidence"] != ingest.NONE


def test_a_personal_deposit_product_is_not_filed_under_business(offline):
    """On four of the five real sites, every personal checking account matched
    "business checking" on the shared word and was reported as business deposit
    onboarding. The word that tells them apart is the one that was left over."""
    for label in ("Free Checking", "Senior Checking", "Now Checking"):
        assert ingest.match_offering(label, "banking").product_key is None
    assert ingest.match_offering(
        "Business Checking", "banking").product_key == "dep-business-deposit"


def test_a_category_heading_does_not_become_a_business_product():
    """A bare "Savings" sits inside the phrase "business savings", which is the
    same trap as "Home" inside "home loans"."""
    assert ingest.match_offering("Savings", "banking").product_key is None


def test_the_crawl_stays_on_the_hosts_own_pages():
    """It is spending requests on someone else's server on a caller's say-so.
    Wandering off-host turns a scan of a bank into a scan of whoever their
    footer links to."""
    links = prospect.candidate_links(
        '<a href="https://other.example/loans">Loans</a>'
        '<a href="/business/lending">Business Lending</a>',
        "https://hillcountry.bank")
    assert links == ["https://hillcountry.bank/business/lending"]
