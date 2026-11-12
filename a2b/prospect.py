"""Scan a real business website and show what their product page would be.

The console's opening move is "paste your URL and we read your website", but
until now the only site it had ever been run against was `bluebonnetbank.com` —
a bundled HTML fixture. So the product page in every demo was one we had written
and then found again, which proves nothing about a stranger's website.

This module is that claim, tested against real sites. Three things it does that
`ingest` on its own does not:

**It reads more than one page.** A bank's homepage carries nav categories —
Personal, Business, Lending — and its actual products live a click away under
`/business/lending`. Scanning only the landing page finds the furniture and
misses the offerings, which is the worst of both.

**It reports their catalog, not our registry.** `ingest.match_scan` answers
"which of our templates does this site want?", and reporting only that showed a
forty-product bank eight rows — the eight our registry already knew. `catalog`
answers "what do they sell?" without a registry at all, and the template match
is recorded against each row instead of deciding which rows exist.

**It writes nothing.** `ingest.save_run` records a run and its proposals so a
vendor can confirm them into skills. Scanning five banks nobody has a
relationship with should not leave five rows behind, and a preview that quietly
creates state is a preview you cannot run twice. Binding still happens through
`ingest.confirm_matches`, which this never calls.

On politeness, since this fetches other people's servers: their robots.txt is
read first and obeyed, same host only, a hard cap on pages, a short timeout, one
request at a time, and an honest User-Agent.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

from . import brand, catalog, ingest, registry, robots

# Pages to read. This used to be six, chosen so a scan finished while someone
# watched it, and it was also the cap on how much of a bank's catalog we could
# report — the products came from the pages we had budget to open.
#
# It is not that any more. `catalog.build` reads the whole catalog out of the
# nav on the first page, so the budget now buys descriptions for offerings we
# have already found rather than the offerings themselves. Twelve is a
# compromise on someone else's server, and a scan of a forty-product bank is
# complete at six pages or at twelve.
MAX_PAGES = 12
PAGE_TIMEOUT = 12

# Link text or URL fragments that suggest a page listing what a business sells.
# Deliberately lexical and domain-flavoured, like the matcher it feeds: this is
# a ranking heuristic for which five links to spend the budget on, not a
# classifier, and being wrong costs one wasted fetch.
_PRODUCT_HINTS = (
    "loan", "lending", "lend", "borrow", "mortgage", "credit", "financing",
    "business", "commercial", "personal", "consumer", "banking", "services",
    "products", "treasury", "deposit", "checking", "savings", "insurance",
    "wealth", "trust", "equipment", "agricultur", "construction",
)

# Banking's product nouns, for the one job that needs them: deciding whether a
# label that the site never linked — a line in a bulleted list, a heading with
# no page behind it — is an offering or a sentence. A nav link does not need
# this test, because its URL is evidence on its own.
#
# Wider than `_PRODUCT_HINTS` on purpose. That tuple ranks links and can afford
# to be crude; this one is the only thing standing between a small bank's
# bulleted service list and being dropped, and "safe deposit boxes", "Zelle"
# and "cashier's checks" carry none of the words in it.
_OFFERING_VOCAB = (
    "loan", "lending", "lend", "borrow", "mortgage", "refinanc", "credit",
    "financ", "account", "checking", "saving", "deposit", "cd", "certificate",
    "ira", "money market", "hsa", "overdraft", "card", "debit", "atm card",
    "wire", "ach", "escrow", "merchant", "payroll", "treasury",
    "cash management", "remote deposit", "positive pay", "sweep", "lockbox",
    "bill pay", "zelle",
    "insurance", "annuit", "brokerage", "investment", "investing", "retirement",
    "wealth", "trust", "estate", "fiduciary", "custody", "equity", "heloc",
    "equipment", "leasing", "agricultur", "farm", "ranch", "construction",
    "sba", "capital", "factoring", "letter of credit", "foreign exchange",
    "safe deposit", "notary", "cashier", "money order", "night drop",
    "online banking", "mobile banking", "digital banking", "e-statement",
    "529", "student", "auto", "vehicle", "boat", "rv", "land", "lot",
)

# Never worth a fetch. Legal boilerplate, contact details and login walls carry
# no offerings, and a login page can cost a redirect chain and a timeout.
_SKIP_HINTS = (
    "login", "signin", "sign-in", "logout", "privacy", "terms", "disclosure",
    "sitemap", "careers", "contact", "locations", "branches", "atm", "news",
    "blog", "event", "press", "search", "accessibility", "fraud", "alert",
    ".pdf", ".jpg", ".png", ".zip", ".doc", "javascript:", "mailto:", "tel:",
)


@dataclass
class Page:
    url: str
    title: str
    scan: ingest.Scan
    # Read off the landing page only; see `brand.read`.
    brand: Optional[dict] = None


@dataclass
class Preview:
    """What this business offers, and which of it we can already build.

    `offerings` is the answer to "what do they sell" and is not filtered by our
    registry. `products` is the subset we hold a template for, kept separate
    because those are the two different questions a reader has and conflating
    them is what made an earlier version of this report so misleading: it showed
    eight products for a bank with forty, and the eight were whichever ones our
    own registry happened to know.
    """
    url: str
    name: str
    industry_key: Optional[str]
    industry_name: str
    # Every industry that scored, best first, so the signup path can offer the
    # runners-up instead of re-reading the site to find them.
    industry_scores: list = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    offerings: list[dict] = field(default_factory=list)
    sections: list[dict] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Their logo and colours, when the site showed them clearly. None is "found
    # nothing", not "found Lenni's".
    brand: Optional[dict] = None

    def as_dict(self) -> dict:
        out = {
            "url": self.url,
            "name": self.name,
            "industry_key": self.industry_key,
            "industry_name": self.industry_name,
            "pages": self.pages,
            "offerings": self.offerings,
            "sections": self.sections,
            "offering_count": len(self.offerings),
            "products": self.products,
            "unmatched": self.unmatched,
            "notes": self.notes,
        }
        if self.brand:
            out["brand"] = self.brand
        return out


def _same_host(a: str, b: str) -> bool:
    ha = urlparse(a).netloc.lower().removeprefix("www.")
    hb = urlparse(b).netloc.lower().removeprefix("www.")
    return bool(ha) and ha == hb


def _normalize(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url.split("#")[0].rstrip("/")


def candidate_links(html: str, base_url: str) -> list[str]:
    """Internal pages most likely to list what this business sells.

    Ranked rather than filtered, because the budget is small and spending it on
    the five best guesses beats taking the first five in document order — on a
    bank site the first links on the page are almost always the login and the
    routing number.
    """
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    for href, text in re.findall(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                                 html, re.I | re.S):
        label = " ".join(re.sub(r"<[^>]+>", " ", text).split()).lower()
        target = _normalize(urljoin(base_url + "/", href.strip()))
        if not target or not _same_host(target, base_url) or target in seen:
            continue
        haystack = f"{label} {target.lower()}"
        if any(s in haystack for s in _SKIP_HINTS):
            continue
        score = sum(1 for hint in _PRODUCT_HINTS if hint in haystack)
        if not score:
            continue
        seen.add(target)
        # Shallow pages first at equal score: /business beats
        # /business/lending/sba/eligibility, and tends to link onward to it.
        found.append((score * 10 - target.count("/"), target))
    found.sort(key=lambda x: -x[0])
    return [u for _, u in found]


def crawl(url: str, max_pages: int = MAX_PAGES) -> list[Page]:
    """The landing page, then the most product-shaped links on it.

    One level deep on purpose. Two levels multiplies the request count against
    someone else's server, and since `catalog.build` names the whole catalog
    from the first page's nav, a second level would buy descriptions rather than
    products. A scan nobody waits for is a scan nobody runs.

    A page that cannot be read is not a page that costs us an offering any
    more — the nav already named it. Failures are skipped silently for that
    reason.

    The landing page also gets its logo and colours read (`brand.read`), with
    the few extra requests that takes going through `get_asset` below.
    """
    import requests

    base = _normalize(url)
    session = requests.Session()
    session.headers["User-Agent"] = robots.USER_AGENT
    # Where each page ended up. A bank that moved to a new domain redirects its
    # old homepage there, and that page's logo lives on the new host.
    landed: dict[str, str] = {}
    site = base

    def get(target: str) -> Optional[str]:
        if not robots.allowed(target):
            return None
        try:
            resp = session.get(target, timeout=PAGE_TIMEOUT)
            resp.raise_for_status()
        except Exception:
            return None
        if "html" not in resp.headers.get("content-type", "text/html").lower():
            return None
        landed[target] = resp.url
        return resp.text

    def get_asset(target: str) -> Optional[brand.Asset]:
        # The same rules as a page, and never off their host — by link or by
        # redirect. Read in chunks so an oversized file costs us its cap, not
        # its size.
        if not _same_host(target, site) or not robots.allowed(target):
            return None
        try:
            resp = session.get(target, timeout=brand.TIMEOUT, stream=True)
            try:
                resp.raise_for_status()
                if not _same_host(resp.url, site):
                    return None
                body = bytearray()
                for chunk in resp.iter_content(64 * 1024):
                    body += chunk
                    if len(body) > brand.MAX_BYTES:
                        return None
                return brand.Asset(url=resp.url, content_type=resp.headers.get("content-type", ""),
                                   body=bytes(body),
                                   headers={k.lower(): v for k, v in resp.headers.items()})
            finally:
                resp.close()
        except Exception:
            return None

    fixture = ingest.demo_fixture(base)
    if fixture is None and not robots.allowed(base):
        raise PermissionError(f"{base} is disallowed by its robots.txt")
    root_html = fixture if fixture is not None else get(base)
    if root_html is None:
        raise RuntimeError(f"could not fetch {base}")

    pages = [Page(base, "", ingest.scan_html(base, root_html))]
    pages[0].title = pages[0].scan.title

    for target in candidate_links(root_html, base)[: max_pages - 1]:
        html = get(target)
        if html is None:
            continue
        scan = ingest.scan_html(target, html)
        pages.append(Page(target, scan.title, scan))

    # A bundled demo host has no server behind it to fetch a logo from.
    if fixture is None:
        site = landed.get(base, base)
        try:
            pages[0].brand = brand.read(site, root_html, get_asset)
        except Exception:
            pages[0].brand = None
    return pages


def site_anchors(pages: list[Page]) -> list[tuple[str, str]]:
    """Every internal link across every page read, as (label, absolute url).

    Resolved per page rather than against the site root, because a relative
    `href="savings"` on `/personal/checking` points at `/personal/savings` and
    resolving it against the root would invent a page that does not exist.
    """
    out: list[tuple[str, str]] = []
    for page in pages:
        for label, href in page.scan.anchors:
            href = (href or "").strip()
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            out.append((label, urljoin(page.url + "/", href)))
    return out


def _merge(pages: list[Page]) -> ingest.Scan:
    """One scan standing for the whole site.

    Offerings are deduplicated by the merged scan's own `offerings` property, so
    a product named in the nav and again on its own page counts once — and the
    longest phrasing of it wins, which is usually the one on the product page
    rather than the nav.
    """
    merged = ingest.Scan(url=pages[0].url, title=pages[0].scan.title,
                         headings=[], links=[], text="", blocks=[])
    texts: list[str] = []
    for page in pages:
        merged.headings.extend(page.scan.headings)
        merged.links.extend(page.scan.links)
        merged.blocks.extend(page.scan.blocks)
        texts.append(page.scan.text)
    merged.text = " ".join(texts)[:200_000]
    return merged


# Titles that name the page rather than the business. Common enough on small
# business sites that without this the scan reports a bank called "Home Page".
_NON_NAMES = {
    "home", "home page", "homepage", "welcome", "index", "main", "start",
    "local bank", "our bank", "personal banking", "business banking", "landing",
}

# Words that turn a title into a description of the business rather than its
# name: "Banking & Lending in Southwest Texas", "A Local Community Bank in TX &
# CO". Both are real bank titles with no separator to split on, so without this
# the scan reports a bank whose name is its own tagline.
#
# "of" is deliberately absent — "Citizens National Bank of Texas" is a name.
_TAGLINE_MARKERS = (" in ", " for ", " with ", " your ", " serving ", " since ")


def _site_name(scan: ingest.Scan, url: str) -> str:
    """The business's name, from the title, minus the tagline it is glued to.

    Titles are written for search engines — "Legend Bank | Personal & Business
    Banking in North Texas" — so the part before the first separator is almost
    always the name and the rest almost never is.

    Entities are unescaped because the title is read straight out of the raw
    HTML rather than through the parser, so `&amp;` survives; a bank shown as
    "Security State Bank &amp; Trust" reads as a bug in us, not in their site.
    """
    title = html.unescape(scan.title or "").strip()
    for sep in ("|", "–", "—", " - ", "::", "•"):
        if sep in title:
            title = title.split(sep)[0]
            break
    title = " ".join(title.split()).strip(" -:")
    lowered = f" {title.lower()} "
    if (2 < len(title) < 60 and title.lower() not in _NON_NAMES
            and not any(m in lowered for m in _TAGLINE_MARKERS)):
        return title
    host = urlparse(_normalize(url)).netloc.removeprefix("www.")
    return host or url


def as_matches(out: Preview) -> list[ingest.Match]:
    """A preview's catalog as ingest proposals, for the signup path.

    The two surfaces read a website for different reasons — this one is about to
    build a vendor's skills, the preview is showing a stranger what we found —
    and they were reading it two different ways, so a bank that scanned its own
    site during signup got a shorter list than the same bank got from our
    scanner. One reader, one answer.

    Everything is a proposal with `confirmed = 0`, including the rows with no
    template: `confirm_matches` turns those into custom skills, which is how a
    product we have never catalogued still becomes something the vendor can
    publish.
    """
    return [
        ingest.Match(
            found_label=row["label"],
            found_quote=row.get("found_quote") or "",
            product_key=row["product_key"],
            template_id=row["template_id"],
            product_name=row["product_name"],
            confidence=row["confidence"],
            score=row["score"],
            matched_phrase=row["matched_phrase"],
        )
        for row in out.offerings
        # A category is the heading above the products, not a service to run an
        # intake for. It would arrive as a skill called "Checking" sitting
        # alongside the seven accounts it names.
        if not row["is_category"]
    ]


def preview(url: str, max_pages: int = MAX_PAGES, name: str = "") -> Preview:
    """Scan a live site and return the product page we would build from it.

    Nothing is written. Run it twice on the same bank and the only trace is in
    their access log.

    `name` is for callers who already know it — the bundled shortlist carries
    the FDIC's name for each bank, which beats anything derivable from a title
    written for search engines. Guessing is the fallback, not the default.
    """
    pages = crawl(url, max_pages=max_pages)
    merged = _merge(pages)
    site = _normalize(url)

    ranked = ingest.detect_industry(merged)
    industry_key = ranked[0][0] if ranked else None
    industry_name = (registry.industries()[industry_key].name
                     if industry_key in registry.industries() else "Unrecognised")

    # Their catalog, in their words, off their own URL structure — before our
    # registry gets an opinion about any of it.
    items = catalog.build(site_anchors(pages), site, _OFFERING_VOCAB)
    items = catalog.add_unlinked(
        items, merged.headings + merged.items, site, _OFFERING_VOCAB)
    items.sort(key=lambda i: (i.section.lower(), i.label.lower()))

    # Now the registry, as an annotation on each row rather than a filter on the
    # list. `best` keeps one row per template so the product table does not
    # repeat "Business Deposit Onboarding" once for every checking account the
    # bank sells, while `offerings` keeps every one of those accounts.
    offerings: list[dict] = []
    best: dict[str, dict] = {}
    for item in items:
        m = ingest.match_offering(item.label, industry_key, merged)
        row = item.as_dict()
        row.update({
            "product_key": m.product_key,
            "product_name": m.product_name,
            "template_id": m.template_id,
            "confidence": m.confidence,
            "score": m.score,
            "matched_phrase": m.matched_phrase,
            "has_template": bool(m.template_id),
            "found_quote": m.found_quote,
        })
        offerings.append(row)
        if m.product_key and m.score > best.get(m.product_key, {}).get("score", -1):
            best[m.product_key] = dict(row, found_label=item.label)

    products = sorted(best.values(), key=lambda r: (-r["score"], r["product_name"]))

    counts: dict[str, int] = {}
    for row in offerings:
        key = row["section"] or "Top level"
        counts[key] = counts.get(key, 0) + 1

    out = Preview(
        url=site,
        name=name.strip() or _site_name(merged, url),
        industry_key=industry_key,
        industry_name=industry_name,
        industry_scores=ranked,
        pages=[p.url for p in pages],
        offerings=offerings,
        sections=[{"name": k, "count": v} for k, v in
                  sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))],
        products=products,
        # The template roadmap: things they sell that we cannot build yet. No
        # longer capped or vocabulary-filtered, because these come from the
        # catalog rather than from every heading on the site, so the noise the
        # cap existed to hide is not in the list to begin with.
        unmatched=[r["label"] for r in offerings
                   if not r["has_template"] and not r["is_category"]],
        brand=pages[0].brand,
    )

    if len(pages) == 1:
        out.notes.append(
            "Only the landing page could be read. Their nav still named the "
            "catalog below — what is missing is the wording from each product "
            "page.")
    if not offerings:
        out.notes.append(
            "We could not find a catalog on this site. Either it is built "
            "entirely in JavaScript, which leaves nothing in the HTML to read, "
            "or its pages are not linked in a way we can follow.")
    elif not products:
        out.notes.append(
            "We found their catalog but hold no template for any of it yet, so "
            "every offering below would start from their wording rather than "
            "from ours.")
    return out
