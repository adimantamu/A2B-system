"""Brand from a scan (#130): the logo and colours a homepage shows.

Offline, like the rest of the scanner's tests. The cases are the ways a real
homepage misleads — the FDIC sign marked up as a logo, a white logo drawn for a
dark header, a framework's stock blue, a dark-mode colour — because the rule
being tested is that a wrong colour is worse than none.
"""
from __future__ import annotations

import io
import random
import sys

import pytest
from fastapi.testclient import TestClient

from a2b import brand, prospect, robots
from a2b.api.main import app

BASE = "https://hillcountry.bank"
NAVY, GOLD = "#0F3B5F", "#C9A227"


def svg(*fills: str) -> bytes:
    shapes = "".join(f'<path d="M0 0h10v10z" fill="{f}"/>' for f in fills)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">{shapes}</svg>'.encode()


def drawing(inner: str, view: str = "0 0 200 40") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}">{inner}</svg>'


# A wide logo: a mark on the left, three letters to its right, unpainted.
MARK = f'<path fill="{GOLD}" d="M0 0h40v40H0z"/>'
LETTERS = '<path d="M50 10h20v20H50z"/><path d="m75 10h20v20h-20z"/><path d="M100 10H120V30H100Z"/>'


def png(w: int, h: int, paint) -> bytes:
    """A PNG whose pixel (x, y) is `paint(x, y)`, an RGBA tuple."""
    from PIL import Image

    img = Image.new("RGBA", (w, h))
    img.putdata([paint(x, y) for y in range(h) for x in range(w)])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def rgba(hexstr: str, a: int = 255) -> tuple:
    return int(hexstr[1:3], 16), int(hexstr[3:5], 16), int(hexstr[5:7], 16), a


def page(head: str = "", body: str = "") -> str:
    return f"<html><head><title>Hill Country State Bank</title>{head}</head><body>{body}</body></html>"


def files(served: dict, calls: list | None = None):
    """A `brand.Fetch` over {path: (content type, body[, headers[, final url]])}."""
    def fetch(url):
        if calls is not None:
            calls.append(url)
        hit = served.get(url.removeprefix(BASE))
        if hit is None:
            return None
        ctype, body, *rest = hit
        headers = rest[0] if rest else {}
        final = rest[1] if len(rest) > 1 else url
        return brand.Asset(url=final, content_type=ctype, body=body, headers=headers)
    return fetch


HEADER_LOGO = '<header><a class="navbar-brand" href="/"><img src="/img/hcsb.svg" alt="Hill Country State Bank"></a></header>'


# --------------------------------------------------------------------------
# Colour values
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("#0f3b5f", NAVY), ("#abc", "#AABBCC"), ("#0F3B5FFF", NAVY),
    ("rgb(15, 59, 95)", NAVY), ("rgb(15 59 95 / 1)", NAVY),
    ("#0F3B5F80", ""), ("rgba(15, 59, 95, .5)", ""), ("navy", ""), ("rgb(300, 0, 0)", ""), ("", ""),
])
def test_a_colour_is_rrggbb_or_nothing(value, expected):
    """The profile takes `#RRGGBB` only. A see-through colour is not the colour
    a customer sees, and a named one is rarely how a site names its brand."""
    assert brand.to_hex(value) == expected


# --------------------------------------------------------------------------
# Which image is the logo
# --------------------------------------------------------------------------

def test_logo_candidates_follow_the_issue_order_one_of_each_kind():
    html = page(
        head='<link rel="apple-touch-icon" sizes="120x120" href="/icons/touch-120.png">'
             '<link rel="apple-touch-icon" sizes="180x180" href="/icons/touch-180.png">'
             '<meta property="og:image" content="https://hillcountry.bank/img/hero-family.jpg">'
             '<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">'
             '<link rel="icon" type="image/svg+xml" href="/favicon.svg">',
        body=HEADER_LOGO + '<main><img src="/img/logo-partner.png" alt="Partner logo"></main>')
    p = brand._Page()
    p.feed(html)
    assert brand.logo_candidates(p, BASE) == [
        ("apple-touch-icon", BASE + "/icons/touch-180.png"),
        ("header image", BASE + "/img/hcsb.svg"),
        ("favicon", BASE + "/favicon.svg"),
        ("favicon", BASE + "/favicon.ico"),
    ]


def test_the_fdic_sign_in_the_header_is_not_the_banks_logo():
    """Banks must show the FDIC's digital sign at the top of the page now, and it
    is routinely marked up `class="fdic-logo"` — the first logo-ish image in the
    header of a great many bank sites."""
    p = brand._Page()
    p.feed(page(body='<header><img class="fdic-logo" src="/img/fdic.svg" alt="FDIC">'
                     '<img class="site-logo" src="/img/hcsb.png" alt="Hill Country State Bank"></header>'))
    assert ("header image", BASE + "/img/hcsb.png") in brand.logo_candidates(p, BASE)
    assert not any("fdic" in url for _, url in brand.logo_candidates(p, BASE))


def test_an_og_image_counts_only_when_its_name_says_logo():
    p = brand._Page()
    p.feed(page(head='<meta property="og:image" content="/uploads/hero-couple-on-porch.jpg">'
                     '<meta property="og:image" content="/uploads/hcsb-logo-square.png">'))
    found = [u for where, u in brand.logo_candidates(p, BASE) if where == "og:image"]
    assert found == [BASE + "/uploads/hcsb-logo-square.png"]


def test_the_plain_header_logo_is_tried_before_its_white_twin():
    """A header often carries a white logo for its dark state ahead of the plain
    one. Only the order changes: a lone logo is tried whatever it is called,
    since "White" is also in bank names."""
    p = brand._Page()
    p.feed(page(body='<header><a class="logo" href="/">'
                     '<img class="desktop" src="/img/hcsb-logo-white.png" alt="Hill Country State Bank">'
                     '<img class="mobile" src="/img/hcsb-logo.png" alt="Hill Country State Bank"></a></header>'))
    assert [u for w, u in brand.logo_candidates(p, BASE) if w == "header image"] == [BASE + "/img/hcsb-logo.png"]

    p = brand._Page()
    p.feed(page(body='<header><img src="/img/white-oak-bank-logo.svg" alt="White Oak Bank"></header>'))
    assert ("header image", BASE + "/img/white-oak-bank-logo.svg") in brand.logo_candidates(p, BASE)


def test_relative_links_resolve_against_the_page_the_way_a_browser_does():
    p = brand._Page()
    p.feed(page(head='<link rel="icon" href="img/favicon.svg">'))
    assert brand.logo_candidates(p, "https://hillcountry.bank/home/index.html") == [
        ("favicon", "https://hillcountry.bank/home/img/favicon.svg"),
        ("favicon", "https://hillcountry.bank/favicon.ico"),
    ]


def test_a_logo_inside_an_unmarked_h1_is_still_at_the_top():
    p = brand._Page()
    p.feed(page(body='<div id="top"><h1><a href="/"><img src="/images/logo.png" alt=""></a></h1></div>'))
    assert ("header image", BASE + "/images/logo.png") in brand.logo_candidates(p, BASE)

    # Below the headline is the page's content: a partner's logo, a badge.
    p = brand._Page()
    p.feed(page(body='<h1>Lending, decided locally.</h1><img src="/img/partner-logo.png" alt="">'))
    assert [w for w, _ in brand.logo_candidates(p, BASE)] == ["favicon"]


def test_another_hosts_image_is_never_a_candidate_nor_a_redirect_target():
    """A CDN or a partner's image is not the business saying who it is."""
    p = brand._Page()
    p.feed(page(head='<link rel="apple-touch-icon" href="https://cdn.example.net/touch.png">'))
    assert all(u.startswith(BASE) for _, u in brand.logo_candidates(p, BASE))

    moved = files({"/img/hcsb.svg": ("image/svg+xml", svg(NAVY), {}, "https://cdn.example.net/hcsb.svg")})
    assert brand.read(BASE, page(body=HEADER_LOGO), moved) is None


# --------------------------------------------------------------------------
# Colours off the logo
# --------------------------------------------------------------------------

def test_an_svg_logo_gives_its_dominant_colour_and_a_contrasting_second():
    got = brand.read(BASE, page(body=HEADER_LOGO),
                     files({"/img/hcsb.svg": ("image/svg+xml", svg(NAVY, NAVY, NAVY, GOLD))}))
    assert got == {"logo": BASE + "/img/hcsb.svg", "primary": NAVY, "accent": GOLD, "source": "scan",
                   "found_in": {"logo": "header image", "primary": "logo", "accent": "logo"}}


def test_a_lighter_shade_of_the_same_colour_is_not_an_accent():
    """Two blues where the site has one would be the scan inventing a palette.
    A tint is far from the original by distance, so it is the hue that decides."""
    got = brand.read(BASE, page(body=HEADER_LOGO),
                     files({"/img/hcsb.svg": ("image/svg+xml", svg(NAVY, NAVY, "#7FA9D1"))}))
    assert got["primary"] == NAVY and got["accent"] == ""


def test_two_second_colours_used_equally_go_to_the_one_set_against_the_first():
    """NBT's logo: a navy name, a red half of Texas and a thin cyan line round
    its star, one shape each. Counting cannot choose; the red is the colour set
    against the navy, whichever the file happens to draw first."""
    for order in (("#00B2E3", "#D02C2F"), ("#D02C2F", "#00B2E3")):
        got = brand.read(BASE, page(body=HEADER_LOGO),
                         files({"/img/hcsb.svg": ("image/svg+xml", svg("#001C71", "#001C71", "#001C71", *order))}))
        assert (got["primary"], got["accent"]) == ("#001C71", "#D02C2F"), order


def test_a_white_logo_drawn_for_a_dark_header_is_passed_over():
    """It would vanish on the light start page. The next candidate gets its turn."""
    html = page(head='<link rel="icon" type="image/svg+xml" href="/favicon.svg">', body=HEADER_LOGO)
    got = brand.read(BASE, html, files({
        "/img/hcsb.svg": ("image/svg+xml", svg("#FFFFFF", "#FFF")),
        "/favicon.svg": ("image/svg+xml", svg(NAVY)),
    }))
    assert got["logo"] == BASE + "/favicon.svg"
    assert got["found_in"]["logo"] == "favicon"


def test_white_lettering_beside_a_coloured_mark_is_passed_over():
    """A coloured mark with the bank's name in white beside it, drawn for a dark
    header (Sonora's and Jefferson's logos). On the light start page only the
    mark would show, so the next candidate gets its turn."""
    lettering = drawing(MARK + f'<g fill="#FFFFFF">{LETTERS}</g>').encode()
    html = page(head='<link rel="icon" type="image/svg+xml" href="/favicon.svg">', body=HEADER_LOGO)
    got = brand.read(BASE, html, files({
        "/img/hcsb.svg": ("image/svg+xml", lettering),
        "/favicon.svg": ("image/svg+xml", svg(NAVY)),
    }))
    assert (got["logo"], got["primary"]) == (BASE + "/favicon.svg", NAVY)


@pytest.mark.parametrize("why,inner", [
    ("inherited from a group", f'{MARK}<g fill="#fff">{LETTERS}</g>'),
    ("a class in a CDATA style sheet",
     f'<style><![CDATA[ /* for the dark header */ .w{{fill:#fff}} ]]></style>{MARK}<g class="w">{LETTERS}</g>'),
    ("a style attribute, which outranks the fill beside it",
     f'{MARK}<g fill="{NAVY}" style="fill:#FFFFFF">{LETTERS}</g>'),
    ("on an outline, which is no ground",
     f'<rect width="200" height="40" fill="none" stroke="{NAVY}"/><g fill="#fff">{LETTERS}</g>'),
])
def test_white_lettering_is_found_however_it_is_painted(why, inner):
    assert brand.svg_vanishes(drawing(inner)), why


@pytest.mark.parametrize("why,inner", [
    ("a badge: white on a coloured ground",
     f'<rect width="200" height="40" fill="{NAVY}"/><g fill="#fff">{LETTERS}</g>'),
    ("a seal: white lettering round the rim of a coloured disc, the name beside it",
     f'<circle cx="20" cy="20" r="20" fill="{NAVY}"/><g fill="#fff"><path d="M4 18h4v4H4z"/>'
     '<path d="M18 3h4v4h-4z"/><path d="M32 18h4v4h-4z"/><path d="M18 33h4v4h-4z"/></g>'
     f'<path fill="{NAVY}" d="M50 10h140v20H50z"/>'),
    ("a card: the logo drawn on a white tile",
     f'<rect width="60" height="40" rx="6" fill="#fff"/><path fill="{NAVY}" d="M10 10h40v20H10z"/>'),
    ("a canvas: white behind the whole drawing",
     f'<rect width="100%" height="100%" fill="white"/><path fill="{NAVY}" d="M10 10h40v20H10z"/>'),
    ("a ground moved into place, which keeps its size",
     f'<g transform="translate(4 0)"><rect width="200" height="40" fill="{NAVY}"/></g>'
     f'<g fill="#fff">{LETTERS}</g>'),
])
def test_white_that_reads_on_a_light_page_is_kept(why, inner):
    assert not brand.svg_vanishes(drawing(inner)), why


def test_an_image_their_server_says_not_to_embed_is_passed_over():
    got = brand.read(BASE, page(body=HEADER_LOGO), files({
        "/img/hcsb.svg": ("image/svg+xml", svg(NAVY), {"cross-origin-resource-policy": "same-origin"}),
    }))
    assert got is None


def test_an_error_page_answering_200_is_not_an_image():
    """It has inline SVG icons in it, which would otherwise be read as the logo."""
    soft_404 = b"<div class='not-found'><svg><path fill='#E11D48'/></svg>Not found</div>"
    assert brand.read(BASE, page(body=HEADER_LOGO),
                      files({"/img/hcsb.svg": ("text/html; charset=utf-8", soft_404)})) is None


def test_an_svg_with_a_comment_before_its_root_is_still_an_svg():
    body = b"<?xml version='1.0'?><!-- Generator: Adobe Illustrator 27 -->" + svg(NAVY)
    got = brand.read(BASE, page(body=HEADER_LOGO),
                     files({"/img/hcsb.svg": ("application/octet-stream", body)}))
    assert got["primary"] == NAVY


def test_a_png_logo_gives_its_colours():
    pytest.importorskip("PIL")
    # 96px square, so the reading is of these pixels and not a resample of them.
    mark = png(96, 96, lambda x, y: rgba(GOLD) if 28 <= x < 68 and 28 <= y < 68 else rgba(NAVY))
    html = page(head='<link rel="apple-touch-icon" href="/apple-touch-icon.png">')
    got = brand.read(BASE, html, files({"/apple-touch-icon.png": ("image/png", mark)}))
    assert (got["logo"], got["primary"], got["accent"]) == (BASE + "/apple-touch-icon.png", NAVY, GOLD)


def test_a_speck_of_colour_in_a_png_is_not_an_accent():
    """A stray few pixels are not a second brand colour: in a PNG the second
    colour has to cover a real share of the logo."""
    pytest.importorskip("PIL")
    mark = png(96, 96, lambda x, y: rgba("#B22225") if x < 6 and y < 6 else rgba(NAVY))
    html = page(head='<link rel="apple-touch-icon" href="/apple-touch-icon.png">')
    got = brand.read(BASE, html, files({"/apple-touch-icon.png": ("image/png", mark)}))
    assert (got["primary"], got["accent"]) == (NAVY, "")


def test_a_photograph_is_not_a_logo():
    pytest.importorskip("PIL")
    rng = random.Random(130)
    photo = png(96, 96, lambda x, y: (rng.randrange(256), rng.randrange(256), rng.randrange(256), 255))
    html = page(head='<meta property="og:image" content="/img/brand-story.png">')
    assert brand.read(BASE, html, files({"/img/brand-story.png": ("image/png", photo)})) is None


def test_a_white_png_on_transparent_and_a_16px_favicon_are_passed_over():
    pytest.importorskip("PIL")
    white = png(96, 96, lambda x, y: (255, 255, 255, 255) if 18 <= y < 48 else (0, 0, 0, 0))
    tiny = png(16, 16, lambda x, y: rgba(NAVY))
    html = page(head='<link rel="apple-touch-icon" href="/touch.png"><link rel="icon" href="/favicon-16.png">')
    got = brand.read(BASE, html, files({"/touch.png": ("image/png", white),
                                        "/favicon-16.png": ("image/png", tiny)}))
    assert got is None


def test_a_png_of_white_lettering_beside_a_coloured_mark_is_passed_over():
    """The same logo as a PNG. Mostly white but far from all of it, so it is the
    white being open to the transparent surround, as the mark is, that gives it
    away."""
    pytest.importorskip("PIL")

    def paint(x, y):
        if x < 30 and 5 <= y < 35:
            return rgba(GOLD)
        if x >= 40 and (x - 40) % 14 < 6 and 8 <= y < 32:
            return 255, 255, 255, 255
        return 0, 0, 0, 0

    html = page(head='<link rel="apple-touch-icon" href="/touch.png">'
                     '<link rel="icon" type="image/svg+xml" href="/favicon.svg">')
    got = brand.read(BASE, html, files({"/touch.png": ("image/png", png(160, 40, paint)),
                                        "/favicon.svg": ("image/svg+xml", svg(NAVY))}))
    assert (got["logo"], got["primary"]) == (BASE + "/favicon.svg", NAVY)


@pytest.mark.parametrize("why,white_inside", [
    ("a white star inside a navy disc", True),
    ("a navy mark on a white disc", False),
])
def test_white_inside_colour_and_colour_on_a_white_disc_are_kept(why, white_inside):
    pytest.importorskip("PIL")

    def paint(x, y):
        if (x - 47.5) ** 2 + (y - 47.5) ** 2 > 46 ** 2:
            return 0, 0, 0, 0
        middle = 28 <= x < 68 and 28 <= y < 68
        return (255, 255, 255, 255) if middle == white_inside else rgba(NAVY)

    html = page(head='<link rel="apple-touch-icon" href="/touch.png">')
    got = brand.read(BASE, html, files({"/touch.png": ("image/png", png(96, 96, paint))}))
    assert (got["logo"], got["primary"]) == (BASE + "/touch.png", NAVY), why


# --------------------------------------------------------------------------
# What the page declares, when the logo has no colour
# --------------------------------------------------------------------------

def test_a_black_logo_is_kept_and_the_colour_comes_from_theme_color():
    html = page(head='<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#1E3A8A">'
                     '<meta name="theme-color" content="#7B1E3A">', body=HEADER_LOGO)
    got = brand.read(BASE, html, files({"/img/hcsb.svg": ("image/svg+xml", svg("#111111"))}))
    assert got["logo"] == BASE + "/img/hcsb.svg"
    assert got["primary"] == "#7B1E3A"
    assert got["found_in"] == {"logo": "header image", "primary": "theme-color"}


def test_brand_custom_properties_count_and_bootstraps_stock_blue_does_not():
    html = page(head="<style>/* tokens */ :root{--bs-primary:#0d6efd;--brand-primary:#14532D;--accent:#B45309}</style>")
    got = brand.read(BASE, html, files({}))
    assert (got["primary"], got["accent"]) == ("#14532D", "#B45309")
    assert got["found_in"] == {"primary": "--brand-primary", "accent": "--accent"}

    stock = page(head="<style>:root{--bs-primary:#0d6efd;--bs-secondary:#6c757d}</style>")
    assert brand.read(BASE, stock, files({})) is None


def test_grey_and_dark_mode_declarations_are_not_a_brand():
    html = page(head="<style>@media (prefers-color-scheme: dark){:root{--primary:#22C55E}}"
                     ":root{--primary:#333333;--accent:#FFFFFF}</style>")
    assert brand.read(BASE, html, files({})) is None


def test_wordpress_presets_count_and_elementors_starter_kit_does_not():
    wp = page(head="<style>body{--wp--preset--color--primary:#9D2235}</style>")
    assert brand.read(BASE, wp, files({}))["primary"] == "#9D2235"

    kit = page(head="<style>.elementor-kit-12{--e-global-color-primary:#6EC1E4;"
                    "--e-global-color-accent:#61CE70}</style>")
    assert brand.read(BASE, kit, files({})) is None


def test_their_own_stylesheet_is_read_and_library_ones_are_not():
    html = page(head='<link rel="stylesheet" href="/wp-includes/css/dist/block-library/style.min.css">'
                     '<link rel="stylesheet" href="/css/bootstrap.min.css">'
                     '<link rel="stylesheet" href="/css/vendor.css">'
                     '<link rel="stylesheet" href="/wp-content/themes/hcsb/theme.css">')
    calls: list[str] = []
    got = brand.read(BASE, html, files({
        "/wp-content/themes/hcsb/theme.css": ("text/css", b":root{--primary:#9D2235}"),
    }, calls))
    assert got["primary"] == "#9D2235"
    assert got["found_in"]["primary"] == "stylesheet --primary"
    assert not any("bootstrap" in c or "block-library" in c for c in calls)


def test_a_coloured_header_is_the_last_resort():
    html = page(head="<style>.site-header{background-color:#0B3D2E}</style>")
    got = brand.read(BASE, html, files({}))
    assert (got["primary"], got["found_in"]) == ("#0B3D2E", {"primary": "header background"})

    inline = page(body='<header style="background: #0B3D2E url(/img/texture.png)">Hi</header>')
    assert brand.read(BASE, inline, files({}))["primary"] == "#0B3D2E"


def test_an_accent_too_close_to_the_primary_is_dropped():
    html = page(head="<style>:root{--primary:#14532D;--accent:#15572F}</style>")
    got = brand.read(BASE, html, files({}))
    assert (got["primary"], got["accent"]) == ("#14532D", "")
    assert "accent" not in got["found_in"]


def test_a_declared_second_colour_in_the_primarys_hue_is_passed_over():
    """Bandera's theme declares a second blue as its secondary. The site's next
    declared colour that is a colour of its own is taken instead; with none,
    no accent beats a second blue."""
    html = page(head="<style>:root{--primary:#062270;--secondary:#3234A1;--bs-secondary:#B8860B}</style>")
    got = brand.read(BASE, html, files({}))
    assert (got["primary"], got["accent"], got["found_in"]["accent"]) == ("#062270", "#B8860B", "--bs-secondary")

    only_blue = page(head="<style>:root{--primary:#062270;--secondary:#3234A1}</style>")
    got = brand.read(BASE, only_blue, files({}))
    assert (got["primary"], got["accent"]) == ("#062270", "")
    assert "accent" not in got["found_in"]


def test_a_second_blue_on_the_page_still_sends_the_scan_to_the_stylesheet():
    html = page(head="<style>:root{--primary:#062270;--secondary:#3234A1}</style>"
                     '<link rel="stylesheet" href="/css/theme.css">')
    got = brand.read(BASE, html, files({"/css/theme.css": ("text/css", b":root{--accent:#B8860B}")}))
    assert (got["accent"], got["found_in"]["accent"]) == ("#B8860B", "stylesheet --accent")


def test_an_accent_alone_is_not_a_brand():
    assert brand.read(BASE, page(head="<style>:root{--accent:#B45309}</style>"), files({})) is None


def test_nothing_clear_means_no_brand_rather_than_a_guess():
    """Not a navy because banks are navy."""
    assert brand.read(BASE, page(body="<h1>Welcome</h1>"), files({})) is None


def test_the_fetch_budget_holds_however_much_the_page_offers():
    """Three images and two stylesheets, whatever the page links to."""
    html = page(
        head='<link rel="apple-touch-icon" href="/a.png"><meta property="og:image" content="/logo-og.png">'
             '<link rel="icon" href="/fav.png">'
             + "".join(f'<link rel="stylesheet" href="/css/site-{i}.css">' for i in range(5)),
        body=HEADER_LOGO)
    calls: list[str] = []
    assert brand.read(BASE, html, files({}, calls)) is None
    assert calls == [BASE + "/a.png", BASE + "/logo-og.png", BASE + "/img/hcsb.svg",
                     BASE + "/css/site-0.css", BASE + "/css/site-1.css"]


# --------------------------------------------------------------------------
# Through the crawl and the scan endpoint
# --------------------------------------------------------------------------

HOME = page(body=HEADER_LOGO + '<nav><a href="/business/lending">Business Lending</a></nav>'
                               "<h1>Lending, decided locally.</h1>")


@pytest.fixture
def site(monkeypatch):
    """The crawl's network, served from a dict: {url: (content type, body[, headers[, final url]])}.

    Anything not in it raises, the way an unreachable file would. robots.txt is
    answered by `disallow`, a set of paths the site refuses.
    """
    served: dict = {}
    disallow: set = set()
    requested: list[str] = []

    class _Resp:
        def __init__(self, url, ctype, body, headers=None, final=None):
            self.url = final or url
            self.headers = {"content-type": ctype, **(headers or {})}
            self._body = body if isinstance(body, bytes) else body.encode()
            self.text = self._body.decode("utf-8", "replace")
        def raise_for_status(self): pass
        def iter_content(self, n):
            for i in range(0, len(self._body), n):
                yield self._body[i:i + n]
        def close(self): pass

    class _Session:
        def __init__(self): self.headers = {}
        def get(self, url, timeout=None, stream=False):
            requested.append(url)
            if url not in served:
                raise RuntimeError(f"unreachable: {url}")
            return _Resp(url, *served[url])

    monkeypatch.setitem(sys.modules, "requests",
                        type("m", (), {"Session": staticmethod(lambda: _Session())}))
    monkeypatch.setattr(robots, "allowed",
                        lambda url, **kw: not any(url.endswith(p) for p in disallow))
    served[BASE] = ("text/html", HOME)
    served[BASE + "/business/lending"] = ("text/html", "<html><body><h2>Equipment loans</h2></body></html>")
    return type("Site", (), {"served": served, "disallow": disallow, "requested": requested})


def test_a_scan_reads_the_brand_off_the_landing_page(site):
    site.served[BASE + "/img/hcsb.svg"] = ("image/svg+xml", svg(NAVY, NAVY, GOLD))
    out = prospect.preview(BASE)
    assert (out.brand["logo"], out.brand["primary"], out.brand["accent"]) == (BASE + "/img/hcsb.svg", NAVY, GOLD)
    assert out.as_dict()["brand"] == out.brand
    assert "lock" not in out.brand, "the lock is the admin's, not the scan's"


def test_the_brand_fetches_obey_robots_and_stay_on_their_host(site):
    site.served[BASE + "/img/hcsb.svg"] = ("image/svg+xml", svg(NAVY))
    site.disallow.add("/img/hcsb.svg")
    site.served[BASE + "/favicon.ico"] = ("image/svg+xml", svg(NAVY), {}, "https://cdn.example.net/favicon.ico")
    out = prospect.preview(BASE)
    assert BASE + "/img/hcsb.svg" not in site.requested
    assert out.brand is None
    assert "brand" not in out.as_dict()


def test_a_homepage_that_moved_host_has_its_logo_read_on_the_new_host(site):
    """fbot.com redirects to fbot.bank, whose page links its logo relative to
    itself. Read against the address typed, every candidate missed."""
    moved = "https://www.hillcountry-bank.com/"
    site.served[BASE] = ("text/html", HOME, {}, moved)
    site.served[moved + "img/hcsb.svg"] = ("image/svg+xml", svg(NAVY, NAVY, GOLD))
    out = prospect.preview(BASE)
    assert (out.brand["logo"], out.brand["primary"]) == (moved + "img/hcsb.svg", NAVY)
    assert BASE + "/img/hcsb.svg" not in site.requested


def test_a_file_over_the_cap_is_dropped(site, monkeypatch):
    monkeypatch.setattr(brand, "MAX_BYTES", 2_000)
    site.served[BASE + "/img/hcsb.svg"] = ("image/svg+xml", svg(*[NAVY] * 200))
    assert prospect.preview(BASE).brand is None


def test_the_scan_endpoint_returns_the_brand_only_when_one_was_found(site):
    client = TestClient(app)
    site.served[BASE + "/img/hcsb.svg"] = ("image/svg+xml", svg(NAVY, NAVY, GOLD))
    found = client.post("/api/ingest/scan", json={"url": BASE}).json()
    assert found["brand"]["primary"] == NAVY and found["brand"]["source"] == "scan"

    del site.served[BASE + "/img/hcsb.svg"]
    assert "brand" not in client.post("/api/ingest/scan", json={"url": BASE}).json()

    # A pasted page has no host behind it to read a logo from.
    pasted = client.post("/api/ingest/scan", json={"url": BASE, "html": HOME}).json()
    assert "brand" not in pasted


def test_the_bundled_demo_bank_scan_is_unchanged(site):
    """`bluebonnetbank.com` is a fixture, not a server. The demo shows its own
    Bluebonnet colours when a scan carries no brand, and this keeps it that way."""
    r = TestClient(app).post("/api/ingest/scan", json={"url": "https://bluebonnetbank.com"})
    r.raise_for_status()
    assert "brand" not in r.json()
    assert not any(u.endswith((".ico", ".svg", ".png", ".css")) for u in site.requested)
