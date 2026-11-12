"""Everything a business sells, read off its own site structure.

`ingest.match_scan` answers "which of our templates does this site want?". That
is the right question when the goal is to build skills, and the wrong one when
the goal is to show a bank what we know about them: it reports the intersection
of their catalog with our registry, so a bank offering forty things saw eight,
and the eight were the ones our registry already knew from the demo fixture.
Any product we had not catalogued was invisible, which reads as a scanner that
cannot see rather than a registry that is young.

This module answers the other question, and it does it without a registry. A
bank's URL layout is a taxonomy the bank maintains itself:

    /business-banking/business-lending/equipment-loans
    /personal/checking/student-cash-rewards-checking

The section is on the left, the product is on the right, and the nav link text
names it in the bank's own words. That is their catalog, stated by them, and it
costs no extra requests — the mega-menu on the homepage carries the whole thing
on every site tried. Completeness therefore no longer depends on how many pages
the crawler had budget to read.

What this cannot do is tell a product from a policy page, so the filtering is
subtractive: everything under a path is an offering unless the path says it is
about the bank, its branches, its news, or its legal disclosures. Being wrong
here costs a row a human unticks, which is the trade `docs/MODEL_RULES.md` rule
2 already assumes — this proposes, the vendor confirms.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import urlparse

# Path words that mean "this page is about the bank" rather than "this is
# something you can buy". Matched as prefixes against the slug's words, so
# `about-us`, `aboutus` and `about` all go, and `meet-our-commercial-lenders`
# goes on `meet`.
_REJECT_PREFIX = (
    "about", "histor", "mission", "leadership", "director", "officer",
    "career", "job", "employ", "testimonial", "biograph", "communit",
    "location", "branch", "hour", "contact", "direction", "news", "blog",
    "press", "article", "podcast", "newsletter", "webinar", "donat", "sponsor",
    "scholarship", "advertis", "charit", "volunteer", "privacy", "disclos",
    "disclaim", "sitemap", "accessib", "cookie", "faq", "tutorial", "calculat",
    "literac", "educat", "resource", "glossar", "fraud", "scam", "spoof",
    "phish", "complaint", "unsubscribe", "login", "logout", "signin",
    "enroll", "register", "applicat", "compare", "overview", "search",
    "switch", "unclaimed", "routing", "shred", "webmail", "inquir", "polic",
    "award", "quote", "survey",
)

# Words that only mean "not a product" when they are the whole word. Prefix
# matching on these would take real products with them: `terms` would reject
# `/business/term-loans`, `rate` would reject `Rate Advantage Checking`, and
# `statement` would reject e-statements, which is a service.
_REJECT_EXACT = {
    "guide", "guides", "help", "support", "demo", "demos", "learn", "learning",
    "tip", "tips", "story", "stories", "media", "event", "events", "atm",
    "atms", "map", "maps", "legal", "terms", "statement", "statements",
    "rate", "rates", "lost", "stolen", "survey", "feedback", "index", "apply",
    "team", "teams", "staff", "board", "meet", "cra", "us", "info", "insight",
    "insights", "faqs", "form", "forms", "alerts", "notice", "notices",
    "update", "updates", "video", "videos", "photo", "photos", "gallery",
    "sitemap", "home", "welcome", "more", "all", "who", "sign",
    # Plumbing, not pages. `/assets/files/truth-in-savings.pdf` is a document
    # the site happens to link, and `assets` stays out of the prefix list so
    # that "Asset Management" survives.
    "assets", "file", "files", "upload", "uploads", "static", "media",
    "download", "downloads", "wp", "cdn", "sites", "default", "content",
}

# Not pages at all.
_REJECT_SUFFIX = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".mp4", ".mp3", ".csv", ".xml",
)

# Link text that names the action rather than the destination. A nav full of
# "Learn More" buttons is normal, and taking that text at face value files four
# products under one meaningless name — so the URL slug names those instead,
# which is what the bank called the page.
_CTA_LABELS = {
    "learn more", "read more", "more", "more info", "more information",
    "details", "view", "view all", "see all", "see more", "explore",
    "get started", "start", "start here", "continue", "next", "go",
    "click here", "here", "apply", "apply now", "apply today", "open",
    "open now", "sign up", "signup", "join", "enroll", "enroll now",
    "learn", "find out more", "discover", "shop", "compare", "calculate",
    "download", "contact us", "call us", "email us", "get a quote",
}

# A label that opens with one of these is describing the click, not the thing:
# "Explore Personal Term Loans", "See Money Market Details", "View Mortgage
# Hub". The slug behind the link is the better name in every case.
_CTA_OPENERS = (
    "explore", "see", "view", "learn", "get", "open", "apply", "find",
    "compare", "discover", "start", "shop", "click", "read", "browse",
    "check", "meet", "request", "download", "visit",
)

# Words that make a phrase an instruction to the reader rather than the name of
# a thing. Used only on labels that were never linked, where the text is all the
# evidence there is.
_CTA_WORDS = (
    "click", "learn", "compare", "find", "get", "see", "start", "explore",
    "discover", "sign", "open", "apply", "contact", "call", "visit", "join",
    "enroll", "download", "request", "schedule", "let us", "we offer",
    "looking for", "ready to", "need a", "want to", "your options",
    # Headings that describe a product rather than name one. They sit on the
    # product's own page, so whatever they are about is in the catalog already.
    "description", "benefit", "feature", "detail", "requirement",
    "eligibilit", "highlight", "summary", "what you", "how it works",
    "informat", "important", "disclos", "policy", "notice", "terms",
)


@dataclass
class Item:
    """One thing the business appears to offer."""
    label: str
    url: str
    section: str
    # True when other pages sit beneath this one — "Checking" above "Free
    # Checking". Categories are kept because they are real offerings and
    # because a bank with a shallow site has nothing else, but the console can
    # show them as headings rather than as products.
    is_category: bool = False
    # Where the label came from: the bank's own nav, or a heading on a page we
    # read. Nav wins on trust, headings reach things the nav does not link.
    source: str = "nav"

    def as_dict(self) -> dict:
        return {"label": self.label, "url": self.url, "section": self.section,
                "is_category": self.is_category, "source": self.source}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


# Words that carry no weight when deciding whether two labels name the same
# thing. Small on purpose: this is for comparing names, not for scoring.
_FILLER = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with",
    "your", "our", "you", "we", "us", "at", "by", "from", "that", "this",
    "it", "is", "are", "all", "more", "other", "options", "option",
}


def _name_key(label: str) -> frozenset[str]:
    """A label reduced to the words that name the thing, plurals flattened."""
    out = set()
    for word in _words(label):
        if word in _FILLER:
            continue
        out.add(word[:-1] if len(word) > 3 and word.endswith("s") else word)
    return frozenset(out)


def _rejected(slug: str) -> bool:
    """Does this path segment name something other than an offering?"""
    for word in _words(slug):
        if word in _REJECT_EXACT:
            return True
        if any(word.startswith(p) for p in _REJECT_PREFIX):
            return True
        # A four-digit year in the path is a dated post, not a product.
        if len(word) == 4 and word.isdigit() and word.startswith(("19", "20")):
            return True
    return False


def _prettify(slug: str) -> str:
    """A readable name from a URL slug, for links whose text was an image."""
    words = re.split(r"[-_+]+", re.sub(r"\.\w+$", "", slug))
    return " ".join(w[:1].upper() + w[1:] for w in words if w)


def _segments(url: str) -> list[str]:
    path = urlparse(url).path
    return [s for s in path.split("/") if s]


def _section_name(segments: list[str]) -> str:
    """The path above the leaf, in the bank's own words.

    Joined rather than reduced to the first segment: `/personal/loans/auto-loans`
    is a consumer loan and `/business/loans/...` is not, and collapsing both to
    "Loans" throws away the distinction the bank drew.
    """
    return " / ".join(_prettify(s) for s in segments[:-1])


def build(anchors: Iterable[tuple[str, str]], site_url: str,
          vocabulary: Optional[Iterable[str]] = None) -> list[Item]:
    """The catalog, from every internal link seen anywhere on the site.

    `anchors` are (label, absolute url) pairs. Duplicates are expected and
    wanted — the same product is linked from the nav of every page, and the
    longest label across those occurrences is usually the fullest phrasing of
    the name.

    `vocabulary` is what a page sitting at the root of the site has to show to
    be believed. A path the bank filed under a section is evidence on its own;
    `/ann-herkenhoff` is not, and one real bank keeps fifteen loan officers
    there, one page each, indistinguishable by URL from a product.
    """
    host = urlparse(site_url).netloc.lower().removeprefix("www.")
    vocab = re.compile(
        r"(?<![a-z])(" + "|".join(re.escape(v) for v in vocabulary) + r")(?![a-z])"
    ) if vocabulary else None

    # url key -> (url as written, label, label came from the slug). The flag
    # matters when the same page is linked twice: a prettified slug is longer
    # than the bank's own wording often enough that "longest wins" alone turned
    # "CDs & IRAs" into "Cds And Iras".
    best: dict[str, tuple[str, str, bool]] = {}
    for label, url in anchors:
        if urlparse(url).netloc.lower().removeprefix("www.") != host:
            continue
        clean = url.split("#")[0].split("?")[0].rstrip("/")
        segments = _segments(clean)
        if not segments:
            continue
        if any(clean.lower().endswith(s) for s in _REJECT_SUFFIX):
            continue
        if any(_rejected(s) for s in segments):
            continue
        if len(segments) == 1 and vocab and not vocab.search(
                f"{label} {segments[0].replace('-', ' ')}".lower()):
            continue
        text = " ".join((label or "").split())
        # Prose in a link is a call to action wrapped around a sentence, and a
        # bare "Learn More" names nothing at all. In both cases the slug is the
        # better name, because the bank chose it for this page specifically.
        words = _words(text)
        from_slug = (not text or len(words) > 6
                     or text.endswith((".", "!", "?"))
                     or text.lower().strip(" -|·:>»→") in _CTA_LABELS
                     or (words and words[0] in _CTA_OPENERS))
        if from_slug:
            text = _prettify(segments[-1])
        if not (2 < len(text) < 70):
            continue
        # Keyed case-insensitively: one bank links the same page as `/roundup`
        # and `/RoundUp`, and listing a service twice reads as a scanner that
        # cannot count.
        key = clean.lower()
        held = best.get(key)
        if (held is None or (held[2] and not from_slug)
                or (held[2] == from_slug and len(text) > len(held[1]))):
            best[key] = (clean, text, from_slug)

    paths = set(best)
    items: list[Item] = []
    for key, (url, label, _) in best.items():
        items.append(Item(
            label=label,
            url=url,
            section=_section_name(_segments(url)),
            # Cheap and exact: something else in the catalog sits under this
            # path, so this is the category above it.
            is_category=any(p != key and p.startswith(key + "/") for p in paths),
        ))
    items.sort(key=lambda i: (i.section.lower(), i.label.lower()))
    return items


def add_unlinked(items: list[Item], labels: Iterable[str], page_url: str,
                 vocabulary: Optional[Iterable[str]] = None,
                 limit: int = 40) -> list[Item]:
    """Offerings named on a page but never linked, appended to the catalog.

    Small banks put their whole service list in one bulleted list and link none
    of it, so the URL taxonomy has nothing to say about them. These are held to
    a much higher bar than a nav link, because a link's path is evidence on its
    own and a heading's text is all there is:

    - it has to carry product vocabulary, on whole words. Substring matching
      here put a loan officer named Yolanda in the catalog, on `land`, and
      `rv` matches `service`;
    - it cannot read as an instruction — "Compare Our Checking Accounts" is a
      page about the products, not a product;
    - it cannot be a sentence, which is the rule `ingest` already applies to
      keep taglines out of the matcher;
    - it cannot read as a document about a product either — "Important Loan
      Information" is the disclosure, not the loan;
    - it cannot be a rewording of something already in the catalog. "Our Cash
      Management Service Offerings" is the marketing headline above the page
      that the nav already called "Cash Management Services", and a catalog
      that lists both looks like it cannot count.
    """
    vocab = [v for v in (vocabulary or ())]
    if not vocab:
        return list(items)
    pattern = re.compile(
        r"(?<![a-z])(" + "|".join(re.escape(v) for v in vocab) + r")(?![a-z])")

    keys = [_name_key(i.label) for i in items]
    out = list(items)
    added = 0
    for raw in labels:
        label = " ".join((raw or "").split()).strip(" -|·:")
        low = label.lower()
        if not (2 < len(label) < 70) or added >= limit:
            continue
        # A name does not end in a full stop. "Digital banking services
        # included." is a sentence from the page's copy that happens to be
        # short enough to pass every other test.
        if label.endswith((".", "!", "?", ",", ";")):
            continue
        if len(_words(label)) > 5 or not pattern.search(low):
            continue
        if any(w in low for w in _CTA_WORDS):
            continue
        key = _name_key(label)
        # Subset as well as superset: the shorter phrasing is no more a new
        # offering than the longer one is.
        if not key or any(key <= k or k <= key for k in keys):
            continue
        keys.append(key)
        added += 1
        out.append(Item(label=label, url=page_url, section="", source="page"))
    return out
