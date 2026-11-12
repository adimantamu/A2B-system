"""What staging serves that production and sandbox don't.

All three sites serve the same build, mirrored from sandbox every minute, so a
preview Doak tries on staging exists only in deploy/a2b-envs: a script tag the
staging block injects into the page, a location that serves the files from
outside the mirrored root, and a link in staging's overlay folder. Each is one
line, and the same line in the production block would put the preview in front
of every bank with nothing else to notice it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy/a2b-envs"
OVERLAY = DEPLOY / "overlay"
SITES = ("production", "staging", "sandbox")
ELSEWHERE = ("production", "sandbox")

THETERMS_SCRIPTS = ["/theterms/engine.js", "/theterms/view.js", "/theterms/theterms.js"]


def _blocks() -> dict[str, str]:
    """Each server block's directives, comment lines dropped, keyed by its root."""
    text = re.sub(r"(?m)^\s*#.*$", "", (DEPLOY / "a2b-envs.conf").read_text(encoding="utf-8"))
    blocks = {}
    for m in re.finditer(r"(?ms)^server \{\n(.*?)^\}", text):
        site = re.search(r"(?m)^\s*root /opt/a2b-envs/(\w+);", m.group(1)).group(1)
        blocks[site] = m.group(1)
    return blocks


BLOCKS = _blocks()


def _injected(site: str) -> list[str]:
    """The scripts a site puts in front of /app.js, in order."""
    m = re.search(r"sub_filter '<script src=\"/app\.js' '([^']*)';", BLOCKS[site])
    assert m, f"{site} injects nothing"
    return re.findall(r'<script src="([^"]+)"', m.group(1))


def _location(site: str, match: str) -> str | None:
    m = re.search(r"location " + re.escape(match) + r" \{(.*?)\}", BLOCKS[site], re.S)
    return m.group(1) if m else None


def test_each_site_has_its_own_server_block():
    assert sorted(BLOCKS) == sorted(SITES)


def test_staging_alone_loads_theterms():
    assert _injected("staging")[:3] == THETERMS_SCRIPTS
    for site in ELSEWHERE:
        assert not any("theterms" in src for src in _injected(site)), site


def test_staging_alone_serves_theterms():
    """From staging's overlay link, and kept out of search results: the
    customer's quote page carries a quote in its address."""
    loc = _location("staging", "^~ /theterms/")
    assert loc and "alias /opt/a2b-envs/overlay/staging/theterms/;" in loc
    assert 'X-Robots-Tag "noindex, nofollow"' in loc
    for site in ELSEWHERE:
        assert "theterms" not in BLOCKS[site].lower(), site


def test_stagings_overlay_alone_links_theterms():
    link = OVERLAY / "staging" / "theterms"
    assert link.is_symlink() and os.readlink(link) == "../theterms"
    for site in ELSEWHERE:
        assert not os.path.lexists(OVERLAY / site / "theterms"), site


def test_every_theterms_file_a_page_asks_for_is_there():
    """The workspace script, the quote page and the website calculator name
    their files by path, and a missing one is a blank page on a customer's
    phone rather than an error anyone sees."""
    asked = set(_injected("staging"))
    for page in ("quote.html", "widget.html"):
        asked |= set(re.findall(r"/theterms/[\w.-]+", (OVERLAY / "theterms" / page).read_text(encoding="utf-8")))
    script = (OVERLAY / "theterms" / "theterms.js").read_text(encoding="utf-8")
    asked |= {"/theterms/" + f for f in re.findall(r"BASE \+ '([\w-]+\.\w+)", script)}
    asked = {p for p in asked if p.startswith("/theterms/")}
    assert {"/theterms/quote.html", "/theterms/widget.html", "/theterms/theterms.css"} <= asked
    missing = sorted(p for p in asked if not (OVERLAY / p.lstrip("/")).is_file())
    assert not missing, missing


def test_staging_alone_loads_and_serves_the_prooflink_preview():
    assert "/prooflink-preview.js" in _injected("staging")
    loc = _location("staging", "= /prooflink-preview.js")
    assert loc and "alias /opt/a2b-envs/overlay/staging/prooflink-preview.js;" in loc
    link = OVERLAY / "staging" / "prooflink-preview.js"
    assert link.is_symlink() and os.readlink(link) == "../prooflink-preview.js"
    for site in ELSEWHERE:
        assert "prooflink" not in BLOCKS[site].lower(), site
        assert not os.path.lexists(OVERLAY / site / "prooflink-preview.js"), site


def test_the_prooflink_button_opens_a_route_only_staging_has():
    """"Open ProofLink" goes to /plaid-demo/, the Plaid Sandbox preview that
    only staging routes, and which stays reachable until #141 folds it into the
    workspace. The button and the route have to be on the same sites, or the
    button opens a page that isn't there."""
    script = (OVERLAY / "prooflink-preview.js").read_text(encoding="utf-8")
    assert "var PREVIEW = '/plaid-demo/';" in script
    assert "include /etc/nginx/snippets/plaid-identity-verification.conf;" in BLOCKS["staging"]
    for site in ELSEWHERE:
        assert "plaid-identity-verification" not in BLOCKS[site], site
