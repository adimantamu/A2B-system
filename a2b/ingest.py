"""Website ingest: scan a vendor's site, propose industry and product matches.

The sales motion is "paste your URL, see your machine", so this is the product's
first impression. It is also the place where it would be easiest to erode trust,
which is why rule 2 exists: ingest *proposes*, the vendor *confirms* every row,
and nothing is ever auto-bound. `confirmed` defaults to 0 in the database and
only a human action flips it.

The matcher is deliberately lexical rather than model-based. Matching a page
heading against a registry of known products is a bounded string problem: the
candidate set is small, the vocabulary is domain-specific, and `site_phrases`
already encodes the phrasings that occur in the wild. A lexical matcher is free,
instant, deterministic and explainable — it can show the vendor exactly which
phrase it matched, which is what makes the confirm step meaningful. Reach for a
model only if measurement shows this failing, and measure it against this
baseline rather than against nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable, Optional

from . import registry, robots
from .config import CONTENT_DIR
from .db import cursor, new_id, now

HIGH, MEDIUM, LOW, NONE = "High", "Medium", "Low", "No match"

_STOP = {
    "a", "an", "and", "the", "for", "of", "to", "in", "on", "with", "your", "our",
    "we", "you", "is", "are", "at", "by", "or", "from", "that", "this", "it",
    "services", "service", "solutions", "options",
}

# Words that are true of nearly every product in their industry. They are real
# signal for "this is a financial service" and no signal at all for "which
# one" — "aircraft financing" and "equipment financing" share only `financing`
# and are not the same product. A token-overlap match therefore has to be
# carried by at least one distinctive word.
_GENERIC = {
    "loan", "loans", "lending", "lend", "financing", "finance", "credit", "banking",
    "bank", "rates", "rate", "apply", "application", "program", "programs",
    "insurance", "coverage", "policy", "legal", "law", "tax", "taxes", "accounting",
    # Words every page in a catalog uses about itself. "Business" and
    # "personal" are deliberately absent: they look like the same kind of noise
    # and they are the opposite — they are the words that tell a business
    # checking account from a personal one.
    "account", "accounts", "product", "products", "option", "options",
    "service", "services",
}


# Site furniture. Every one of these is a nav link on nearly every business
# website, and none of them is an offering — but several are fragments of real
# product phrases, which is how "Home" came to be matched to a residential
# mortgage at high confidence on a live bank site.
#
# Matched against the whole label rather than its tokens, so "Home" is rejected
# and "Home equity line" is not. Anything with real words around it survives.
_NAV_FURNITURE = {
    "home", "about", "about us", "contact", "contact us", "locations", "branches",
    "careers", "search", "menu", "login", "log in", "sign in", "sign up", "logout",
    "privacy", "security", "terms", "disclosures", "sitemap", "help", "support",
    "faq", "faqs", "news", "blog", "events", "resources", "calculators", "rates",
    "apply", "apply now", "open account", "open an account", "get started",
    "learn more", "read more", "more", "personal", "business", "commercial",
    "consumer", "banking", "lending", "loans", "products", "services", "accounts",
    "online banking", "mobile banking", "skip to content", "skip to main content",
    "bank", "banks", "borrow", "invest", "investing", "save", "saving",
}


def _is_a_sentence(label: str) -> bool:
    """Is this prose that happens to sit in a heading, rather than a name?

    A product is named in a few words. Anything much longer is a tagline or a
    fragment of marketing copy, and those brush against product vocabulary
    constantly — "Serving the great state of Texas for four generations" is not
    a trust product, and "Credit card options at Security State Bank & Trust"
    is not one either, though the bank's own name puts the word in reach.
    """
    return len(_tokens(label)) > 5


def _is_furniture(label: str) -> bool:
    return " ".join((label or "").lower().split()).strip(" -|·:") in _NAV_FURNITURE


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _STOP and len(w) > 1}


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

# Tags whose text names something rather than describing it. `li`, `dt`,
# `summary` and `option` are here because a bank lists its services in a
# bulleted list or an accordion at least as often as in a run of headings, and
# every one of those was invisible while only headings and links were read.
_LABEL_TAGS = {
    "h1": "heading", "h2": "heading", "h3": "heading",
    "h4": "heading", "h5": "heading", "h6": "heading",
    "a": "link",
    "li": "item", "dt": "item", "summary": "item", "option": "item",
    "button": "item", "figcaption": "item",
}

_IGNORED_TAGS = {"script", "style", "noscript", "template", "svg"}

# Tags the HTML spec lets an author leave unclosed, plus `a`, which cannot
# nest. Without this, `<li>Auto Loans<li>Home Loans` reads as the second item
# nested inside the first and the two labels come out glued together.
_PEER_TAGS = {"li", "dt", "dd", "option", "td", "th", "p", "a"}

# Elements with no closing tag. They must not go on the stack, because a frame
# that is never popped swallows the rest of the document into one label.
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}


class _SiteParser(HTMLParser):
    """Pulls labels — headings, links, list items — and the visible body.

    Text is accumulated per element and flushed when the element closes, rather
    than attributed to whichever tag opened most recently. The difference is not
    cosmetic: `<h2><span>Commercial Real Estate Loans</span></h2>` is how a CMS
    emits a heading, and reading the innermost tag threw the whole heading away.
    Against real bank sites that silently dropped most of the catalog, which
    read as "the scanner found nothing" rather than as a parser bug.

    Text lands on every open label, so `<li><a>Auto Loans</a></li>` yields both
    the item and the link instead of one at the other's expense. Duplicates are
    resolved later, by `Scan.offerings`.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self.links: list[str] = []
        self.items: list[str] = []
        # (label, href) for every link that has one. The href matters as much as
        # the text: a bank's URL layout is a taxonomy it maintains itself, and
        # `/business-banking/business-lending/equipment-loans` says what the
        # link text alone cannot.
        self.anchors: list[tuple[str, str]] = []
        self.text: list[str] = []
        # (kind, text) in document order. Keeping the order is what lets a
        # heading be paired with the paragraph underneath it.
        self.blocks: list[tuple[str, str]] = []
        self._ignoring = 0
        # Every open element, innermost last: [tag, kind or None, parts, href].
        # Containers are tracked as well as labels, so that `</ul>` closing over
        # an unclosed `<li>` ends the item — otherwise the rest of the page is
        # read as part of that one list item.
        self._open: list[list] = []

    def handle_starttag(self, tag, attrs):
        if tag in _IGNORED_TAGS:
            self._ignoring += 1
            return
        if self._ignoring or tag in _VOID_TAGS:
            return
        if tag in _PEER_TAGS:
            self._close(tag)
        kind = _LABEL_TAGS.get(tag)
        href = dict(attrs).get("href") or "" if tag == "a" else ""
        self._open.append([tag, kind, [], href])

    def handle_endtag(self, tag):
        if tag in _IGNORED_TAGS:
            self._ignoring = max(0, self._ignoring - 1)
            return
        if not self._ignoring:
            self._close(tag)

    def _close(self, tag: str) -> None:
        """Close the innermost open `tag`, flushing any label left open inside.

        A stray closing tag with nothing to match is ignored rather than
        unwinding the stack: real pages carry them, and treating one as the end
        of an enclosing section would truncate everything after it.
        """
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i][0] == tag:
                for entry in self._open[i:]:
                    self._flush(entry)
                del self._open[i:]
                return

    def _flush(self, entry: list) -> None:
        _, kind, parts, href = entry
        if kind is None:
            return
        label = " ".join(" ".join(parts).split())
        if kind == "link" and href:
            # Recorded even when the text is empty — an image link to
            # `/business/sba-loans` still tells us the page exists, and the
            # slug can name it.
            self.anchors.append((label, href))
        if not label:
            return
        if kind == "heading":
            self.headings.append(label)
        elif kind == "link":
            self.links.append(label)
        else:
            self.items.append(label)
        self.blocks.append((kind, label))

    def handle_data(self, data):
        if self._ignoring:
            return
        s = " ".join(data.split())
        if not s:
            return
        self.text.append(s)
        labels = [e for e in self._open if e[1] is not None]
        if not labels:
            self.blocks.append(("text", s))
            return
        for entry in labels:
            entry[2].append(s)

    def close(self):
        # A page that ends mid-element is common enough on truncated or
        # hand-edited HTML, and the labels already collected are still good.
        super().close()
        for entry in self._open:
            self._flush(entry)
        self._open.clear()


@dataclass
class Scan:
    url: str
    title: str
    headings: list[str]
    links: list[str]
    text: str
    blocks: list = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    anchors: list = field(default_factory=list)

    def blurb_for(self, offering: str) -> str:
        """The vendor's own copy sitting under a heading.

        Proximity beats similarity here. A service description rarely repeats
        the words in its own heading — "Apartment & multifamily lending" is
        followed by "Financing for 5+ unit properties", which shares no
        distinctive token with it — so searching the page for a sentence that
        looks like the heading finds the wrong sentence or none at all.
        """
        for i, (kind, text) in enumerate(self.blocks):
            if kind != "heading" or text != offering:
                continue
            for next_kind, following in self.blocks[i + 1:i + 5]:
                # Stop at the next heading. Running past one means attributing
                # the following service's description to this one, which reads
                # as a confident, wrong quote on the confirmation screen.
                if next_kind == "heading":
                    break
                if next_kind == "text" and 12 <= len(following) < 220:
                    return following
        return ""

    @property
    def offerings(self) -> list[str]:
        """Candidate service names, de-duplicated, longest form kept.

        Nav furniture is dropped here rather than left for the matcher to score.
        Even with the fragment rule tightened, a bare "Business" still shares a
        distinctive token with "business checking" and lands a Low match, and a
        confirmation screen listing "Business" as a possible product is noise
        the vendor has to read and dismiss.
        """
        seen: dict[str, str] = {}
        for raw in self.headings + self.links + self.items:
            s = raw.strip(" -|·")
            if not (3 < len(s) < 70) or _is_furniture(s):
                continue
            key = " ".join(sorted(_tokens(s)))
            if not key:
                continue
            if key not in seen or len(s) > len(seen[key]):
                seen[key] = s
        return list(seen.values())


def scan_html(url: str, html: str) -> Scan:
    p = _SiteParser()
    p.feed(html)
    p.close()
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = " ".join(m.group(1).split())
    return Scan(url=url, title=title, headings=p.headings, links=p.links,
                text=" ".join(p.text)[:200_000], blocks=p.blocks,
                items=p.items, anchors=p.anchors)


def demo_fixture(url: str) -> Optional[str]:
    """HTML for a bundled demo host, or None.

    `bluebonnetbank.com` is not a real domain, so the walkthrough everyone runs
    first would fail at the network. Checking a fixture directory keeps that
    path exercising the real parser and the real matcher — only the transport
    is short-circuited.
    """
    host = re.sub(r"^https?://", "", url.strip()).split("/")[0].lower()
    path = CONTENT_DIR / "demo_sites" / f"{host}.html"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def fetch(url: str, timeout: int = 15) -> Scan:
    """Fetch and scan a live site. Kept separate from `scan_html` so tests and
    the matcher never need the network."""
    fixture = demo_fixture(url)
    if fixture is not None:
        return scan_html(url, fixture)

    import requests

    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not robots.allowed(url):
        raise PermissionError(f"{url} is disallowed by its robots.txt")
    resp = requests.get(url, timeout=timeout, headers={
        "User-Agent": robots.USER_AGENT
    })
    resp.raise_for_status()
    return scan_html(url, resp.text)


# --------------------------------------------------------------------------
# Industry detection
# --------------------------------------------------------------------------

def detect_industry(scan: Scan) -> list[tuple[str, float]]:
    """Rank industries by signal hits. Always a suggestion — never binding."""
    haystack = f"{scan.title} {' '.join(scan.headings)} {scan.text[:20000]}".lower()
    scored: list[tuple[str, float]] = []
    for key, ind in registry.industries().items():
        hits = sum(1 for sig in ind.signals if sig in haystack)
        if hits:
            scored.append((key, hits / max(len(ind.signals), 1)))
    scored.sort(key=lambda x: -x[1])
    return scored


# --------------------------------------------------------------------------
# Product matching
# --------------------------------------------------------------------------

@dataclass
class Match:
    found_label: str
    found_quote: str
    product_key: Optional[str]
    template_id: Optional[str]
    product_name: Optional[str]
    confidence: str
    score: float
    matched_phrase: Optional[str] = None


def _phrase_score(offering: str, phrase: str) -> float:
    """How well one registry phrase explains one offering.

    An exact substring is decisive. Otherwise fall back to token overlap scaled
    by how much of the registry phrase was accounted for, which keeps a
    three-word phrase from winning on one incidental shared word.

    Overlap made up entirely of generic domain words scores zero. Without that,
    every product in an industry is a weak match for every offering in it,
    because they all say "loans" or "financing", and the vendor is handed a
    confirmation screen full of plausible-looking nonsense.

    Containment is tested in both directions, and the reverse direction is the
    dangerous one. `phrase in offering` is strong evidence — a heading that
    contains "commercial real estate" is about commercial real estate. `offering
    in phrase` is the opposite claim: that a short label is explained by a longer
    product name it happens to sit inside. That is true for "HELOC" against
    "HELOC line of credit", and false for "Home" against "home loans" — and on
    five real bank sites the false version bound nav buttons to mortgage
    products at full confidence. Both directions are kept, and the reverse one
    has to get past `_is_furniture` first.
    """
    o, p = offering.lower(), phrase.lower()
    if p in o:
        return 1.0
    # Past here the evidence is shared vocabulary rather than the phrase itself,
    # and against prose that is not evidence at all. Two real examples, both
    # scored as services the bank offers:
    #
    #   "Credit card options at Security State Bank & Trust" -> Trust Services
    #   "Serving the great state of Texas for four generations" -> Trust Services
    #
    # The first is the bank's own name; the second is a tagline against a
    # tagline, since the registry itself lists "serving families for
    # generations". A sentence has enough words to brush against something, so
    # a sentence only matches when it actually contains the phrase.
    if _is_a_sentence(o):
        return 0.0
    ot, pt = _tokens(o), _tokens(p)
    if not ot or not pt:
        return 0.0
    # Reverse containment, and only for a label specific enough to carry the
    # claim. "HELOC" inside "HELOC line of credit" is the same product named
    # briefly; "Savings" inside "business savings" is a category heading being
    # read as a business product, which is how every personal checking account
    # on a real bank site came back filed under business onboarding.
    if o in p and not _is_furniture(o) and (len(ot) > 1 or offering.isupper()):
        return 1.0
    overlap = ot & pt
    if not overlap or not (overlap - _GENERIC):
        return 0.0
    # The phrase's own distinguishing words have to be accounted for. Sharing
    # the head noun is not enough: "business checking" and "Free Checking" are
    # both checking accounts and are not the same product, and the word that
    # says so — "business" — is exactly the one left over.
    if pt - ot - _GENERIC:
        return 0.0
    return len(overlap) / len(pt) * 0.9


def match_offering(offering: str, industry_key: Optional[str] = None,
                   scan: Optional[Scan] = None) -> Match:
    best_score, best_product, best_phrase = 0.0, None, None
    candidates: Iterable[registry.Product] = (
        registry.products_for_industry(industry_key) if industry_key
        else registry.products().values()
    )
    for product in candidates:
        for phrase in product.site_phrases:
            s = _phrase_score(offering, phrase)
            if s > best_score:
                best_score, best_product, best_phrase = s, product, phrase

    if best_score >= 0.85:
        conf = HIGH
    elif best_score >= 0.5:
        conf = MEDIUM
    elif best_score >= 0.34:
        conf = LOW
    else:
        conf = NONE

    quote = scan.blurb_for(offering) if scan else ""
    if conf == NONE or best_product is None:
        return Match(offering, quote, None, None, None, NONE, round(best_score, 3))
    return Match(
        found_label=offering,
        found_quote=quote,
        product_key=best_product.key,
        template_id=best_product.template,
        product_name=best_product.name,
        confidence=conf,
        score=round(best_score, 3),
        matched_phrase=best_phrase,
    )


def match_scan(scan: Scan, industry_key: Optional[str] = None) -> list[Match]:
    """Score every offering, then resolve collisions in favour of the best score.

    Scoring must happen before de-duplication. A page's h1 tagline ("Lending,
    decided locally") will often brush against a product phrase and score just
    enough to register; if it is processed first and allowed to claim the
    product, the actual service heading further down the page is discarded as a
    duplicate. Resolving by score means the strongest evidence for a product
    wins regardless of where it sits on the page.
    """
    scored = [match_offering(o, industry_key, scan) for o in scan.offerings]
    scored.sort(key=lambda m: (-m.score, m.found_label))

    out: list[Match] = []
    claimed: set[str] = set()
    for m in scored:
        if m.product_key:
            if m.product_key in claimed:
                continue
            claimed.add(m.product_key)
        out.append(m)
    return out


# --------------------------------------------------------------------------
# Persistence — proposals, then confirmation
# --------------------------------------------------------------------------

def save_run(vendor_id: Optional[str], scan: Scan, industry_key: Optional[str],
             matches: list[Match]) -> str:
    run_id = new_id("ing")
    with cursor() as cur:
        cur.execute(
            "INSERT INTO ingest_run (id, vendor_id, url, industry_key, industry_confirmed,"
            " created_at) VALUES (?,?,?,?,0,?)",
            (run_id, vendor_id, scan.url, industry_key, now()),
        )
        for m in matches:
            cur.execute(
                "INSERT INTO ingest_match (id, run_id, found_label, found_quote, product_key,"
                " template_id, confidence, score, confirmed, skill_id)"
                " VALUES (?,?,?,?,?,?,?,?,0,NULL)",
                (new_id("mch"), run_id, m.found_label, m.found_quote, m.product_key,
                 m.template_id, m.confidence, m.score),
            )
    return run_id


def get_run(run_id: str) -> Optional[dict]:
    with cursor() as cur:
        run = cur.execute("SELECT * FROM ingest_run WHERE id=?", (run_id,)).fetchone()
        if not run:
            return None
        rows = cur.execute(
            "SELECT * FROM ingest_match WHERE run_id=? ORDER BY score DESC", (run_id,)
        ).fetchall()
    d = dict(run)
    d["matches"] = [dict(r) for r in rows]
    return d


def confirm_industry(run_id: str, industry_key: str) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE ingest_run SET industry_key=?, industry_confirmed=1 WHERE id=?",
            (industry_key, run_id),
        )


def confirm_matches(run_id: str, vendor_id: str, match_ids: list[str]) -> list[str]:
    """Turn confirmed proposals into draft skills.

    Draft, not published — the vendor still has to read what their machine will
    say before any customer or agent can reach it.
    """
    from .skills import create_custom_skill, seed_skill_from_template

    created: list[str] = []
    run = get_run(run_id)
    if not run:
        raise KeyError(run_id)
    wanted = set(match_ids)
    for m in run["matches"]:
        if m["id"] not in wanted or m["confirmed"]:
            continue
        if m["template_id"]:
            skill_id = seed_skill_from_template(vendor_id, m["template_id"])
        else:
            # No template yet. It still becomes a real skill, and the unmatched
            # label is what feeds the template roadmap.
            skill_id = create_custom_skill(vendor_id, m["found_label"])
        with cursor() as cur:
            cur.execute("UPDATE ingest_match SET confirmed=1, skill_id=? WHERE id=?",
                        (skill_id, m["id"]))
        created.append(skill_id)
    return created


def unmatched_demand(limit: int = 50) -> list[dict]:
    """Offerings we could not match, aggregated across every ingest ever run.

    This is the template roadmap, and it is the quiet reason ingest is worth
    building well: every vendor who scans a site tells us which template to
    write next.
    """
    with cursor() as cur:
        rows = cur.execute(
            "SELECT found_label, COUNT(*) AS n FROM ingest_match"
            " WHERE product_key IS NULL GROUP BY LOWER(found_label)"
            " ORDER BY n DESC, found_label LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"label": r["found_label"], "count": r["n"]} for r in rows]
