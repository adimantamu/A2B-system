"""Reading a business's whole catalog, rather than the part we already knew.

Two failures on real bank sites are behind this file, and both were invisible in
the test suite because every test scanned a fixture we had written ourselves.

The first was that the parser attributed text to whichever tag opened last, so
`<h2><span>Commercial Real Estate Loans</span></h2>` — how a CMS emits a
heading — threw the heading away. The fixtures used bare headings, so nothing
caught it.

The second was that the report was the intersection of a bank's catalog with our
registry. A bank with forty offerings was shown eight, and the eight were the
ones our registry knew from the demo fixture, so the report looked like the limit
of their website when it was the limit of our library.
"""
from __future__ import annotations

from a2b import catalog, ingest

# Markup shaped like a real bank's, rather than like a fixture: headings wrapped
# in spans, an icon inside the link, a service list that never closes its `li`,
# and the catalog carried in the nav rather than in the body.
MESSY = """
<html><head><title>Bluebonnet Bank | Personal &amp; Business Banking</title></head>
<body>
<nav>
  <a href="/"><img src="/logo.png"></a>
  <a href="/personal/checking/free-checking"><span>Free Checking</span></a>
  <a href="/personal/checking/senior-checking">Senior Checking</a>
  <a href="/business/lending/equipment-loans"><i class="ico"></i><span>Equipment Loans</span></a>
  <a href="/business/lending/sba-loans">Learn More</a>
  <a href="/about-us/our-history">Our History</a>
  <a href="/locations/fredericksburg">Fredericksburg</a>
  <a href="/news/2026/new-hire">Two New Leadership Appointments</a>
  <a href="/privacy">Privacy Notice</a>
  <a href="/resources/calculators">Calculators</a>
  <a href="https://other.example/loans">Loans elsewhere</a>
</nav>
<h2><span>Commercial Real Estate Loans</span></h2>
<p>Own the building your business calls home.</p>
<h3>Other <em>services</em> we offer</h3>
<ul>
  <li>Safe Deposit Boxes
  <li>Notary Services
  <li>Wire Transfers
</ul>
<h2>Serving the great state of Texas for four generations</h2>
</body></html>
"""


def scan():
    return ingest.scan_html("https://bluebonnetbank.com", MESSY)


def build():
    s = scan()
    anchors = [(label, "https://bluebonnetbank.com" + href) if href.startswith("/")
               else (label, href) for label, href in s.anchors]
    items = catalog.build(anchors, "https://bluebonnetbank.com")
    return catalog.add_unlinked(items, s.headings + s.items,
                               "https://bluebonnetbank.com",
                               ("loan", "deposit", "notary", "wire", "checking"))


# --------------------------------------------------------------------------
# The parser
# --------------------------------------------------------------------------

def test_a_heading_wrapped_in_a_span_is_still_a_heading():
    """The bug that made the scanner look blind. Every CMS on the sites tried
    wraps heading text in at least one inline element, and reading the innermost
    open tag dropped the whole heading."""
    assert "Commercial Real Estate Loans" in scan().headings


def test_a_heading_split_by_an_inline_tag_is_not_truncated():
    """`<h3>Other <em>services</em> we offer</h3>` used to arrive as "Other"."""
    assert "Other services we offer" in scan().headings


def test_a_link_wrapped_in_a_span_keeps_its_text_and_its_url():
    assert ("Equipment Loans", "/business/lending/equipment-loans") in scan().anchors


def test_list_items_are_read_as_offerings():
    """A small bank's whole service list is often a `ul` and nothing else. None
    of it was visible while only headings and links were collected."""
    assert "Safe Deposit Boxes" in scan().items
    assert "Notary Services" in scan().items


def test_an_unclosed_list_item_does_not_swallow_the_rest_of_the_page():
    """`<li>` needs no closing tag. Without treating `</ul>` as the end of the
    item, the first bullet came back carrying every word that followed it."""
    assert "Wire Transfers" in scan().items
    for item in scan().items:
        assert len(item) < 60, item


def test_a_description_under_a_heading_is_still_quotable():
    """The proximity rule `blurb_for` depends on: it needs the paragraph to
    arrive as its own block rather than folded into an unclosed element."""
    assert "calls home" in scan().blurb_for("Commercial Real Estate Loans")


# --------------------------------------------------------------------------
# The catalog
# --------------------------------------------------------------------------

def labels():
    return {i.label for i in build()}


def test_products_come_from_the_sites_own_url_structure():
    """No registry involved. These are products because the bank filed them
    under a product path, which is a claim the bank makes, not one we infer."""
    assert {"Free Checking", "Senior Checking", "Equipment Loans"} <= labels()


def test_a_product_we_hold_no_template_for_is_still_in_the_catalog():
    """The whole point. Seven personal checking accounts against one business
    deposit template used to mean the report showed one row."""
    assert "Senior Checking" in labels()
    matched = ingest.match_offering("Senior Checking", "banking")
    assert matched.product_key is None


def test_the_section_is_the_path_the_bank_filed_it_under():
    equip = next(i for i in build() if i.label == "Equipment Loans")
    assert equip.section == "Business / Lending"


def test_pages_about_the_bank_are_not_offerings():
    """History, branches, press releases and legal notices sit on the same nav
    as the products and are not things anyone can buy."""
    for junk in ("Our History", "Fredericksburg", "Privacy Notice",
                 "Two New Leadership Appointments", "Calculators"):
        assert junk not in labels(), junk


def test_another_companys_links_are_not_this_companys_catalog():
    assert not any("other.example" in i.url for i in build())


def test_a_learn_more_link_is_named_by_its_url():
    """A nav full of "Learn More" buttons is normal. Taking the text at face
    value files several products under one meaningless name."""
    assert "Sba Loans" in labels()
    assert "Learn More" not in labels()


def test_a_service_that_is_only_ever_listed_is_still_found():
    """Never linked, so the URL taxonomy says nothing about it. A bulleted list
    under a heading is how small banks publish their service list."""
    assert {"Safe Deposit Boxes", "Notary Services", "Wire Transfers"} <= labels()


def test_a_tagline_is_not_a_service():
    assert not any("four generations" in l for l in labels())


def test_a_category_above_other_products_is_marked_as_one():
    """`/personal/checking` is both a real offering and the heading above seven
    accounts, and a report that cannot tell them apart double-counts."""
    items = catalog.build(
        [("Checking", "https://b.com/personal/checking"),
         ("Free Checking", "https://b.com/personal/checking/free-checking")],
        "https://b.com")
    by_label = {i.label: i for i in items}
    assert by_label["Checking"].is_category
    assert not by_label["Free Checking"].is_category


def test_a_wordier_restatement_of_a_product_is_not_a_second_product():
    """"Our Cash Management Service Offerings" is the headline on the page the
    nav already called "Cash Management Services"."""
    items = catalog.build(
        [("Cash Management Services", "https://b.com/business/cash-management")],
        "https://b.com")
    out = catalog.add_unlinked(
        items, ["Our Cash Management Service Offerings", "Cash Management"],
        "https://b.com", ("cash management",))
    assert len(out) == 1
