"""A business's logo and brand colours, read off its own homepage.

The customer-facing pages — the start page, the embed on a bank's site, the
apply flow — are skinned with the business's colours and logo so they look like
the bank's own, while Lenni's chrome stays Lenni's. The org profile holds those
(Admin → General), and a scan fills it when it is empty. This is the reading
half: the same crawl that finds the catalog, asked one more question.

## Conservative on purpose

A wrong colour is worse than none. An empty field shows Lenni's defaults and
says nothing; a guessed navy on a bank whose brand is green is a claim, made in
their name, on a page their customers see. So every field is either something
the site itself declared or something measured off its own logo, and when
neither is clear the field stays empty. Nothing here knows what colour banks
usually are.

**The logo**, in this order, first one that holds up:

1. `apple-touch-icon` — the square mark a site chose to stand for itself.
2. `og:image`, only when its address names a logo. Most are photographs.
3. An `<img>` in the header whose alt, class, id or address says logo, brand
   or wordmark — and not the FDIC sign, which banks now put in their header
   and mark up as a logo.
4. The favicon, as a last resort.

Same host only, by link and by redirect: a CDN or a partner's image is not the
business saying who it is, and it can change under us. A candidate is dropped
when it could not be shown on another site (`Cross-Origin-Resource-Policy`), is
too small to read, is a photograph, or is a white logo made for a dark header
that would vanish on a light page.

**Colours**: the dominant one and a contrasting second from that logo. When the
logo has none — black, white, grey — the page's own declarations stand in:
`theme-color`, brand custom properties on `:root`, then a coloured header.
Stock framework values (Bootstrap's blue, Elementor's starter kit) are not a
brand, and nor is white, black or grey.

## Cost to their server

The landing page is already read. On top of it: at most three images and two
stylesheets, same host, robots.txt obeyed, a short timeout and a size cap, one
at a time. Fetching is the caller's (`prospect.crawl`), so all of that is
decided in one place.
"""
from __future__ import annotations

import colorsys
import io
import math
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

TIMEOUT = 6
MAX_BYTES = 1_000_000
MAX_LOGO_FETCHES = 3
MAX_CSS_FETCHES = 2


@dataclass
class Asset:
    """One fetched file: where it ended up, what it says it is, and its bytes."""
    url: str
    content_type: str
    body: bytes
    headers: dict


Fetch = Callable[[str], Optional[Asset]]

# What a logo calls itself. Matched against alt, class, id and the address.
_LOGO_WORDS = re.compile(r"logo|wordmark|brand", re.I)
# A mark in an og:image address. Wider than the header test, because an
# og:image is a photograph unless its name says otherwise.
_MARK_WORDS = re.compile(r"logo|wordmark|brand|emblem|seal|\bmark\b|[-_/]mark[-_.]|icon", re.I)
# Logos that are not the business's own. Banks must now show the FDIC's digital
# sign at the top of the page, and it is routinely `class="fdic-logo"`.
_NOT_THEIRS = re.compile(
    r"fdic|ncua|equal[-_ ]?housing|\behl\b|insured|\bbbb\b|award|badge|\bzelle\b|\bvisa\b|"
    r"mastercard|apple[-_ ]?pay|google[-_ ]?pay|app[-_ ]?store|google[-_ ]?play|\bnmls\b|"
    r"forbes|newsweek|best[-_ ]of|\bratings?\b", re.I)
# How a site names the white version of its logo. Only used to choose between
# two header logos, never to refuse one: "white" is also in bank names.
_REVERSED = re.compile(r"white|revers|knock-?out|invers|negative", re.I)
# Containers that are the top of the page, and the one that is not.
_HEADER_HINT = re.compile(r"header|masthead|navbar|nav-bar|top-?bar|site-brand|logo|brand", re.I)
_FOOTER_HINT = re.compile(r"footer", re.I)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "param", "source", "track", "wbr"}

# Custom properties a site uses to name its own colours, best evidence first.
_PRIMARY_VARS = (
    "--brand-primary", "--brand-color", "--brand", "--color-brand", "--primary-color",
    "--color-primary", "--primary", "--theme-primary", "--wp--preset--color--primary",
    "--e-global-color-primary", "--bs-primary",
)
_ACCENT_VARS = (
    "--brand-accent", "--accent-color", "--color-accent", "--accent", "--brand-secondary",
    "--secondary-color", "--color-secondary", "--secondary", "--wp--preset--color--accent",
    "--wp--preset--color--secondary", "--e-global-color-accent", "--e-global-color-secondary",
    "--bs-secondary",
)
# Values that come with a framework rather than from a brand: Bootstrap 4 and 5,
# and Elementor's starter kit. A site that never customised them did not choose
# them.
_STOCK = {
    "#0D6EFD", "#007BFF", "#6C757D", "#6610F2", "#6F42C1", "#D63384", "#DC3545",
    "#FD7E14", "#FFC107", "#198754", "#20C997", "#0DCAF0", "#28A745", "#17A2B8",
    "#6EC1E4", "#54595F", "#7A7A7A", "#61CE70",
}
_HEADER_SELECTORS = {
    "header", ".header", "#header", ".site-header", "#site-header", "#masthead",
    ".masthead", ".navbar", "nav", ".top-bar", ".topbar", ".main-header",
    ".header-main", ".header-wrapper",
}
# Stylesheets that are someone else's library, never where a brand is declared.
_LIBRARY_CSS = re.compile(
    r"bootstrap|font-?awesome|jquery|slick|swiper|animate|normalize|reset|wp-includes|"
    r"block-library|dashicons|fonts\.googleapis|icons?\.css", re.I)
_LIKELY_CSS = re.compile(r"custom|brand|theme|style|main|global|site|app|kit|post-\d", re.I)


# --------------------------------------------------------------------------
# Colour arithmetic
# --------------------------------------------------------------------------

def to_hex(value: str) -> str:
    """`#RRGGBB` for a CSS colour written as hex or rgb(), else empty.

    Named colours are not read: a site that writes `navy` in a stylesheet is
    rarely naming its brand with it.
    """
    v = (value or "").strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            if len(h) == 4 and h[3] != "f":
                return ""
            h = "".join(c * 2 for c in h[:3])
        elif len(h) == 8:
            if h[6:] != "ff":
                return ""
            h = h[:6]
        elif len(h) != 6:
            return ""
        return "#" + h.upper()
    m = re.fullmatch(r"rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})\s*(?:[,/]\s*([\d.]+%?))?\s*\)", v)
    if m:
        alpha = m.group(4)
        if alpha is not None and alpha not in ("1", "1.0", "100%"):
            return ""
        rgb = [int(m.group(i)) for i in (1, 2, 3)]
        if any(c > 255 for c in rgb):
            return ""
        return "#%02X%02X%02X" % tuple(rgb)
    return ""


def _rgb(hexstr: str) -> tuple[int, int, int]:
    return int(hexstr[1:3], 16), int(hexstr[3:5], 16), int(hexstr[5:7], 16)


def _hsv(rgb) -> tuple[float, float, float]:
    return colorsys.rgb_to_hsv(rgb[0] / 255, rgb[1] / 255, rgb[2] / 255)


def chromatic(rgb) -> bool:
    """A colour a brand could be: not white, black or grey."""
    _, s, v = _hsv(rgb)
    return s >= 0.2 and v >= 0.15


def _near_white(rgb) -> bool:
    _, s, v = _hsv(rgb)
    return v >= 0.9 and s <= 0.15


def _lab(rgb) -> tuple[float, float, float]:
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def distance(a, b) -> float:
    """CIE76 ΔE: about 2 is just noticeable, 12 is a different shade."""
    return math.dist(_lab(a), _lab(b))


def _hue_apart(a, b) -> float:
    d = abs(_hsv(a)[0] - _hsv(b)[0]) * 360
    return min(d, 360 - d)


def _contrasting(a, b) -> bool:
    """A second colour that reads as another colour, not a shade of the first.

    It has to differ in hue, not only in lightness: a lighter shade of the same
    blue is the anti-aliasing round the letters, or the same colour in a tint,
    and calling it an accent would put two blues where the site has one.
    """
    return _hue_apart(a, b) >= 20 and distance(a, b) >= 30


def _clusters(weighted: list[tuple[tuple[int, int, int], float]]) -> list[tuple[tuple[int, int, int], float]]:
    """Colours merged into shades, heaviest first, each with its share."""
    total = sum(w for _, w in weighted) or 1.0
    groups: list[list] = []          # [seed, weight, sum_r, sum_g, sum_b]
    for rgb, w in sorted(weighted, key=lambda x: -x[1]):
        for g in groups:
            if distance(g[0], rgb) < 12:
                g[1] += w
                g[2] += rgb[0] * w
                g[3] += rgb[1] * w
                g[4] += rgb[2] * w
                break
        else:
            groups.append([rgb, w, rgb[0] * w, rgb[1] * w, rgb[2] * w])
    out = [((round(g[2] / g[1]), round(g[3] / g[1]), round(g[4] / g[1])), g[1] / total)
           for g in groups]
    return sorted(out, key=lambda x: -x[1])


def _pick(clusters, primary_share: float, accent_share: float) -> tuple[str, str]:
    """The dominant colour and a contrasting second, or empty where unclear."""
    if not clusters or clusters[0][1] < primary_share:
        return "", ""
    primary = clusters[0][0]
    options = [(rgb, share) for rgb, share in clusters[1:]
               if share >= accent_share and _contrasting(primary, rgb)]
    if not options:
        return "#%02X%02X%02X" % primary, ""
    # Used equally — one shape each, as often in an SVG — the counts cannot say
    # which is the second colour; the one set furthest from the first is.
    tied = [rgb for rgb, share in options if abs(share - options[0][1]) < 1e-9]
    accent = max(tied, key=lambda rgb: distance(primary, rgb))
    return "#%02X%02X%02X" % primary, "#%02X%02X%02X" % accent


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

class _Page(HTMLParser):
    """The parts of a homepage that say what the business looks like."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self.metas: list[dict] = []
        self.imgs: list[dict] = []
        self.styles: list[str] = []
        self.header_bgs: list[str] = []
        self._open: list[tuple[str, str]] = []   # (tag, class + id)
        self._in_style = False
        self._seen_main = False

    def _context(self) -> tuple[bool, bool, str]:
        header = footer = False
        hints = []
        for tag, hint in self._open:
            if tag == "footer" or _FOOTER_HINT.search(hint):
                footer = True
            if tag in ("header", "nav") or _HEADER_HINT.search(hint):
                header = True
            hints.append(hint)
        return header, footer, " ".join(hints)

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        hint = f"{a.get('class', '')} {a.get('id', '')}".strip()
        if tag == "link":
            self.links.append(a)
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "img":
            header, footer, around = self._context()
            src = a.get("src", "")
            if not src or src.startswith("data:"):
                src = a.get("data-src") or a.get("data-lazy-src") or ""
                if not src and a.get("srcset"):
                    src = a["srcset"].split(",")[0].strip().split(" ")[0]
            self.imgs.append({"src": src, "alt": a.get("alt", ""), "hint": hint, "around": around,
                              "header": header, "footer": footer, "top": not self._seen_main})
        elif tag == "style":
            self._in_style = True
        if tag == "main":
            self._seen_main = True
        if "style" in a and (tag in ("header", "nav") or _HEADER_HINT.search(hint)):
            m = re.search(r"background(?:-color)?\s*:\s*([^;]+)", a["style"], re.I)
            if m:
                self.header_bgs.append(m.group(1))
        if tag not in _VOID:
            self._open.append((tag, hint))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and self._open and self._open[-1][0] == tag:
            self._open.pop()

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False
        if tag == "h1":
            # Not at its start: a logo is often the page's <h1>.
            self._seen_main = True
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i][0] == tag:
                del self._open[i:]
                break

    def handle_data(self, data):
        if self._in_style:
            self.styles.append(data)


def _same_host(a: str, b: str) -> bool:
    ha = urlparse(a).netloc.lower().removeprefix("www.")
    hb = urlparse(b).netloc.lower().removeprefix("www.")
    return bool(ha) and ha == hb


def _size(sizes: str) -> int:
    best = 0
    for token in (sizes or "").lower().split():
        m = re.fullmatch(r"(\d+)x(\d+)", token)
        if m:
            best = max(best, min(int(m.group(1)), int(m.group(2))))
    return best


def _rels(link: dict) -> set[str]:
    return set(link.get("rel", "").lower().split())


def logo_candidates(page: _Page, base: str) -> list[tuple[str, str]]:
    """(where it was found, absolute url), in the order they are tried.

    `base` is the page's own address, after any redirect: relative links
    resolve against it, as they do in a browser.
    """
    out: list[tuple[str, str]] = []

    def add(found: str, href: str):
        href = (href or "").strip()
        if not href or href.startswith(("data:", "javascript:")):
            return
        url = urljoin(base, href)
        if _same_host(url, base) and url not in (u for _, u in out):
            out.append((found, url))

    # One of each kind: the other sizes of an icon are the same picture, and
    # spending the fetch budget on them would leave the header logo untried.
    touch = [l for l in page.links if _rels(l) & {"apple-touch-icon", "apple-touch-icon-precomposed"}]
    if touch:
        add("apple-touch-icon", max(touch, key=lambda l: _size(l.get("sizes", ""))).get("href", ""))

    for meta in page.metas:
        if (meta.get("property") or meta.get("name") or "").lower() in ("og:image", "og:image:url"):
            content = meta.get("content", "")
            path = urlparse(content).path
            if _MARK_WORDS.search(path) and not _NOT_THEIRS.search(path):
                add("og:image", content)

    logos = []
    for img in page.imgs:
        if img["footer"] or not (img["header"] or img["top"]):
            continue
        said = " ".join((img["alt"], img["hint"], img["src"], img["around"]))
        own = " ".join((img["alt"], img["hint"], img["src"]))
        if _LOGO_WORDS.search(said) and not _NOT_THEIRS.search(own):
            logos.append(img)
    if logos:
        # A header often carries a white logo for its dark state beside the
        # plain one; the plain one is the one that reads on a light page.
        plain = [i for i in logos if not _REVERSED.search(" ".join((i["src"], i["hint"], i["around"])))]
        add("header image", (plain or logos)[0]["src"])

    icons = [l for l in page.links if "icon" in _rels(l) and not _rels(l) & {"apple-touch-icon", "mask-icon"}]
    icons.sort(key=lambda l: (0 if "svg" in l.get("type", "").lower() or l.get("href", "").lower().endswith(".svg")
                              else 1, -_size(l.get("sizes", ""))))
    if icons:
        add("favicon", icons[0].get("href", ""))
    add("favicon", "/favicon.ico")
    return out


# --------------------------------------------------------------------------
# The logo
# --------------------------------------------------------------------------

def _media_type(asset: Asset) -> str:
    return asset.content_type.lower().split(";")[0].strip()


def _is_svg(asset: Asset) -> bool:
    if "svg" in _media_type(asset):
        return True
    head = asset.body[:4096].lower()
    # Editors put a comment or a doctype before the root, so look for it rather
    # than expecting it first.
    return b"<svg" in head and b"<html" not in head


_SVG_PAINT = re.compile(r"(fill|stroke|stop-color)\s*[:=]\s*[\"']?\s*(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\))")


def _svg_styles(text: str) -> list[str]:
    """The SVG's style sheets, without comments or the CDATA wrapper some
    exporters put round them."""
    return [re.sub(r"/\*.*?\*/|<!\[CDATA\[|\]\]>", "", block, flags=re.S)
            for block in re.findall(r"<style[^>]*>(.*?)</style>", text, re.S | re.I)]


def svg_colours(text: str) -> list[tuple[tuple[int, int, int], float]]:
    """Paint in an SVG, weighted by how many shapes use it.

    Counted rather than measured: an SVG cannot be rasterised here, and a logo
    is a handful of shapes in a handful of colours, which counting gets right.
    """
    by_class: dict[str, list[tuple[str, float]]] = {}
    for block in _svg_styles(text):
        for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", block):
            paints = [(to_hex(v), 0.5 if k == "stroke" else 1.0) for k, v in _SVG_PAINT.findall(body)]
            for sel in selectors.split(","):
                sel = sel.strip()
                if sel.startswith("."):
                    by_class.setdefault(sel[1:], []).extend(p for p in paints if p[0])
    weighted: list[tuple[tuple[int, int, int], float]] = []
    for tag in re.findall(r"<(?:path|rect|circle|ellipse|polygon|polyline|line|text|tspan|stop|use|g)\b[^>]*>",
                          text, re.I):
        paints = [(to_hex(v), 0.5 if k.lower() in ("stroke", "stop-color") else 1.0)
                  for k, v in _SVG_PAINT.findall(tag)]
        m = re.search(r"\bclass\s*=\s*[\"']([^\"']+)", tag)
        if m:
            for cls in m.group(1).split():
                paints.extend(by_class.get(cls, []))
        for hx, w in paints:
            if hx:
                weighted.append((_rgb(hx), w))
    return weighted


# What an SVG draws where it stands, and what it only defines for use elsewhere.
_SVG_SHAPES = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line", "text", "use", "image"}
_SVG_UNDRAWN = {"defs", "clippath", "mask", "symbol", "pattern", "marker", "lineargradient",
                "radialgradient", "filter", "title", "desc", "metadata", "style"}
_SVG_NAMED = {"white": "#FFFFFF", "black": "#000000"}
_NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
_PATH_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|" + _NUMBER)
_PATH_ARGS = {"m": 2, "l": 2, "h": 1, "v": 1, "c": 6, "s": 4, "q": 4, "t": 2, "a": 7}


def _num(value: str) -> Optional[float]:
    m = re.match(r"\s*(" + _NUMBER + r")\s*(?:px)?\s*$", value or "")
    return float(m.group(1)) if m else None


def _path_box(d: str) -> Optional[tuple[float, float, float, float]]:
    """A path's extent, control points included: enough to tell a backdrop
    from a letter, which is all it is used for."""
    xs: list[float] = []
    ys: list[float] = []
    x = y = sx = sy = 0.0
    cmd, args = "", []
    for tok in _PATH_TOKEN.findall(d or ""):
        if tok.isalpha():
            cmd, args = tok, []
            if tok in "Zz":
                x, y = sx, sy
            continue
        if not cmd or cmd in "Zz":
            return None
        args.append(float(tok))
        kind, rel = cmd.lower(), cmd.islower()
        if len(args) < _PATH_ARGS[kind]:
            continue
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if kind == "h":
            x = ox + args[0]
        elif kind == "v":
            y = oy + args[0]
        elif kind == "a":
            x, y = ox + args[5], oy + args[6]
        else:
            pairs = list(zip(args[0::2], args[1::2]))
            for px, py in pairs[:-1]:
                xs.append(ox + px)
                ys.append(oy + py)
            x, y = ox + pairs[-1][0], oy + pairs[-1][1]
        xs.append(x)
        ys.append(y)
        if kind == "m":
            sx, sy = x, y
            cmd = "l" if rel else "L"
        args = []
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _shape_box(tag: str, a: dict) -> Optional[tuple[float, float, float, float]]:
    if tag in ("rect", "image"):
        w, h = a.get("width", ""), a.get("height", "")
        if w.strip() == "100%" and h.strip() == "100%":
            return (float("-inf"), float("-inf"), float("inf"), float("inf"))
        x, y, w, h = _num(a.get("x", "0")), _num(a.get("y", "0")), _num(w), _num(h)
        return None if None in (x, y, w, h) else (x, y, x + w, y + h)
    if tag in ("circle", "ellipse"):
        cx, cy = _num(a.get("cx", "0")), _num(a.get("cy", "0"))
        rx = _num(a.get("r") or a.get("rx", ""))
        ry = _num(a.get("r") or a.get("ry", ""))
        return None if None in (cx, cy, rx, ry) else (cx - rx, cy - ry, cx + rx, cy + ry)
    if tag in ("polygon", "polyline"):
        nums = [float(n) for n in re.findall(_NUMBER, a.get("points", ""))]
        if len(nums) < 4:
            return None
        return (min(nums[0::2]), min(nums[1::2]), max(nums[0::2]), max(nums[1::2]))
    if tag == "path":
        return _path_box(a.get("d", ""))
    return None


class _SvgShapes(HTMLParser):
    """Every shape an SVG draws, with the paint it ends up with.

    Paint is inherited, so a group's `fill="#fff"` whitens every letter in it;
    and a style rule outranks the attribute it sits beside. Shapes inside
    `<defs>` and the like are drawn only where something uses them.
    """

    def __init__(self, rules: dict[str, dict[str, str]]):
        super().__init__(convert_charrefs=True)
        self.rules = rules
        self.view: Optional[tuple[float, float]] = None
        self.shapes: list[tuple[str, Optional[tuple], bool]] = []  # (white | colour | none, extent, filled)
        self._stack: list[tuple[str, str, str, bool, bool]] = []    # tag, fill, stroke, moved, undrawn

    def _paint(self, a: dict, prop: str) -> str:
        m = re.search(r"(?:^|;)\s*" + prop + r"\s*:\s*([^;]+)", a.get("style", ""), re.I)
        if m:
            return m.group(1).strip()
        for cls in a.get("class", "").split():
            if prop in self.rules.get(cls, {}):
                return self.rules[cls][prop]
        return a.get(prop, "").strip()

    def _start(self, tag: str, attrs, push: bool):
        a = {k: (v or "") for k, v in attrs}
        tag_, fill_, stroke_, moved_, undrawn_ = (self._stack[-1] if self._stack
                                                 else ("", "#000000", "none", False, False))
        fill = self._paint(a, "fill") or fill_
        stroke = self._paint(a, "stroke") or stroke_
        # A shift leaves a shape's size alone, which is all the extent is for.
        transform = a.get("transform", "").strip()
        moved = moved_ or bool(transform and not re.fullmatch(r"translate\([^)]*\)", transform))
        undrawn = undrawn_ or tag in _SVG_UNDRAWN
        if tag == "svg" and self.view is None:
            box = [float(n) for n in re.findall(_NUMBER, a.get("viewbox", ""))]
            w, h = (box[2], box[3]) if len(box) == 4 else (_num(a.get("width", "")), _num(a.get("height", "")))
            if w and h and w > 0 and h > 0:
                self.view = (w, h)
        if tag in _SVG_SHAPES and not undrawn:
            stroked = tag == "line" or fill.lower() in ("none", "transparent")
            paint = stroke if stroked else fill
            if tag == "image":
                kind = "colour"
            elif paint.lower() in ("none", "transparent", ""):
                kind = "none"
            else:
                hx = _SVG_NAMED.get(paint.lower()) or to_hex(paint)
                kind = "white" if hx and _near_white(_rgb(hx)) else "colour"
            self.shapes.append((kind, None if moved else _shape_box(tag, a), not stroked))
        if push:
            self._stack.append((tag, fill, stroke, moved, undrawn))

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, push=True)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, push=False)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break


def svg_vanishes(text: str) -> bool:
    """Whether an SVG is mostly white shapes standing on nothing: lettering
    drawn for a dark header, which leaves a stray mark on a light page.

    White that reads anywhere does not count: white on a coloured backdrop
    (a badge, or the lettering round a seal), and a white canvas or card the
    logo is drawn on, which goes on a light page and leaves the logo whole.
    Only extents are known here, not outlines, so "on" means "inside the
    extent of a filled shape drawn earlier".
    """
    rules: dict[str, dict[str, str]] = {}
    for block in _svg_styles(text):
        for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", block):
            props = {k.lower(): v.strip() for k, v in re.findall(r"(?:^|;)\s*(fill|stroke)\s*:\s*([^;]+)", body, re.I)}
            for sel in selectors.split(","):
                sel = sel.strip()
                if re.fullmatch(r"\.[\w-]+", sel):
                    rules.setdefault(sel[1:], {}).update(props)
    walk = _SvgShapes(rules)
    try:
        walk.feed(text)
        walk.close()
    except Exception:
        return False
    shapes = [s for s in walk.shapes if s[0] != "none"]
    if len(shapes) > 500:
        return False                 # an illustration, and the pairs below would cost seconds
    area = walk.view[0] * walk.view[1] if walk.view else 0.0
    slack = 0.01 * max(walk.view) if walk.view else 0.0

    def size(box) -> float:
        return (box[2] - box[0]) * (box[3] - box[1])

    def inside(box, outer) -> bool:
        return (outer[0] - slack <= box[0] and outer[1] - slack <= box[1]
                and box[2] <= outer[2] + slack and box[3] <= outer[3] + slack)

    drawn = exposed = 0
    under: list[tuple] = []          # extents of the filled colour drawn so far
    for n, (kind, box, filled) in enumerate(shapes):
        if kind == "white" and box and (
                (area and size(box) >= 0.5 * area)
                or any(k == "colour" and b and inside(b, box) for k, b, _ in shapes[n + 1:])):
            continue                 # a canvas or card under the logo
        drawn += 1
        if kind == "colour":
            if box and filled:
                under.append(box)
        elif not any((area and size(b) >= 0.5 * area) or (box and inside(box, b)) for b in under):
            exposed += 1
    return drawn > 0 and exposed >= 0.5 * drawn


def _svg_logo(asset: Asset) -> Optional[tuple[str, str]]:
    try:
        text = asset.body.decode("utf-8")
    except UnicodeDecodeError:
        text = asset.body.decode("latin-1")
    if "<svg" not in text.lower():
        return None
    paints = svg_colours(text)
    if paints and all(_near_white(rgb) for rgb, _ in paints):
        return None                       # drawn for a dark header
    if svg_vanishes(text):
        return None                       # white lettering beside a coloured mark
    colour = [(rgb, w) for rgb, w in paints if chromatic(rgb)]
    clusters = _clusters(colour)
    if len(clusters) > 4:
        return "", ""                     # an illustration, not a palette
    return _pick(clusters, primary_share=0.3, accent_share=0.0)


def _pixels(img) -> list[tuple[int, int, int, int]]:
    """An RGBA image's pixels, row by row. `getdata` is deprecated from Pillow
    12 and gone in 14, and its replacement is not in 11; the raw bytes are the
    same in all of them."""
    b = img.tobytes()
    return list(zip(b[0::4], b[1::4], b[2::4], b[3::4]))


def _white_vanishes(img) -> bool:
    """Whether an image is white lettering beside a coloured mark, both
    standing on transparency: drawn for a dark header, it leaves the mark
    alone on a light page.

    White is counted only where the transparent surround reaches it without
    crossing colour, so a star inside a navy circle is not. And the colour has
    to meet the surround too: colour inside a white disc or card is a logo on
    a card, which loses only the card on a light page. An image with no
    transparency brought its own background and is never caught here.
    """
    small = img.copy()
    small.thumbnail((192, 192))          # thin strokes survive; 96px would blur them away
    w, h = small.size
    data = _pixels(small)

    def white(p) -> bool:
        hi, lo = max(p[:3]), min(p[:3])
        return hi >= 230 and (hi - lo) <= 0.15 * hi

    def neighbours(i: int):
        x = i % w
        return ((i - 1) if x else -1, (i + 1) if x < w - 1 else -1, i - w, i + w)

    clear = bytearray(1 if p[3] < 128 else 0 for p in data)
    ink = len(data) - sum(clear)
    seen = bytearray(clear)
    todo = [i for i in range(len(data)) if clear[i]]
    reached = 0
    while todo:
        for j in neighbours(todo.pop()):
            if 0 <= j < len(data) and not seen[j] and white(data[j]):
                seen[j] = 1
                reached += 1
                todo.append(j)
    edge = colour_edge = 0
    for i, p in enumerate(data):
        if not clear[i] and any(0 <= j < len(data) and clear[j] for j in neighbours(i)):
            edge += 1
            colour_edge += 0 if white(p) else 1
    return ink > 0 and reached >= 0.3 * ink and colour_edge >= 0.05 * edge


def _raster_logo(asset: Asset) -> Optional[tuple[str, str]]:
    """The logo's colours, or None when this image should not be the logo."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(asset.body))
        img.load()
    except Exception:
        return None
    w, h = img.size
    if min(w, h) < 24 or max(w, h) > 8 * min(w, h):
        return None                       # a 16px favicon, or a banner
    img = img.convert("RGBA")
    if _white_vanishes(img):
        return None                       # white lettering beside a coloured mark
    img.thumbnail((96, 96))
    px = _pixels(img)
    opaque = [p[:3] for p in px if p[3] >= 200]
    if len(opaque) < 64:
        return None
    if len(opaque) < len(px) * 0.9 and sum(1 for p in opaque if _near_white(p)) >= 0.85 * len(opaque):
        return None                       # white on transparent: made for a dark header
    if len({(r >> 3, g >> 3, b >> 3) for r, g, b in opaque}) > 1200:
        return None                       # a photograph
    colour = [p for p in opaque if chromatic(p)]
    if len(colour) < max(24, 0.03 * len(opaque)):
        return "", ""                     # black, white or grey: the page says the rest
    bins: dict[tuple[int, int, int], list[int]] = {}
    for r, g, b in colour:
        acc = bins.setdefault((r >> 3, g >> 3, b >> 3), [0, 0, 0, 0])
        acc[0] += r
        acc[1] += g
        acc[2] += b
        acc[3] += 1
    weighted = [((acc[0] // acc[3], acc[1] // acc[3], acc[2] // acc[3]), float(acc[3]))
                for acc in bins.values()]
    return _pick(_clusters(weighted), primary_share=0.25, accent_share=0.08)


def _logo(asset: Asset) -> Optional[tuple[str, str]]:
    """(primary, accent) from a usable logo, either possibly empty; None if unusable."""
    corp = (asset.headers.get("cross-origin-resource-policy") or "").lower()
    if corp in ("same-origin", "same-site"):
        return None                       # their server forbids showing it elsewhere
    kind = _media_type(asset)
    if kind.startswith("text/html") or kind.startswith("application/json"):
        return None                       # an error page answering 200
    if _is_svg(asset):
        return _svg_logo(asset)
    raster = asset.body[:4] in (b"\x89PNG", b"GIF8", b"\x00\x00\x01\x00", b"RIFF") or asset.body[:2] == b"\xff\xd8"
    if not (raster or kind.startswith("image/")):
        return None
    return _raster_logo(asset)


# --------------------------------------------------------------------------
# What the page declares
# --------------------------------------------------------------------------

def _usable(css: str) -> str:
    """CSS without comments or dark-mode blocks, whose colours are not the brand's."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out, i = [], 0
    for m in re.finditer(r"@media[^{]*prefers-color-scheme\s*:\s*dark[^{]*\{", css, re.I):
        if m.start() < i:
            continue
        out.append(css[i:m.start()])
        depth, j = 1, m.end()
        while j < len(css) and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        i = j
    out.append(css[i:])
    return "".join(out)


# Where a site declares its colours for the whole page. WordPress puts its
# presets on `body`; Elementor puts its global colours on its kit class.
_PAGE_WIDE = re.compile(r":root|html|body|\.elementor-kit-\d+", re.I)


def root_vars(css: str) -> dict[str, str]:
    """Custom properties declared for the whole page."""
    found: dict[str, str] = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _usable(css)):
        if not any(_PAGE_WIDE.fullmatch(s.strip()) for s in selectors.split(",")):
            continue
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", body):
            found.setdefault(name.lower(), value.strip())
    return found


def _resolve(value: str, props: dict[str, str], depth: int = 0) -> str:
    m = re.fullmatch(r"var\(\s*(--[\w-]+)\s*(?:,\s*(.+))?\)", value.strip(), re.I)
    if not m:
        return to_hex(value)
    if depth < 3 and m.group(1).lower() in props:
        got = _resolve(props[m.group(1).lower()], props, depth + 1)
        if got:
            return got
    return _resolve(m.group(2), props, depth + 1) if m.group(2) else ""


def _declared_all(props: dict[str, str], names: tuple[str, ...]) -> list[tuple[str, str]]:
    """Every named property holding a brand colour, with its name, best first."""
    out = []
    for name in names:
        if name in props:
            hx = _resolve(props[name], props)
            if hx and hx not in _STOCK and chromatic(_rgb(hx)):
                out.append((hx, name))
    return out


def _declared(props: dict[str, str], names: tuple[str, ...]) -> tuple[str, str]:
    """The first named property holding a brand colour, and its name."""
    found = _declared_all(props, names)
    return found[0] if found else ("", "")


def _second(primary: str, options: list[tuple[str, str]]) -> tuple[str, str]:
    """The first option that reads as a colour of its own beside `primary`.

    A site's own `--secondary` can be a second blue as easily as the logo's
    tint can, so declared colours meet the same test as measured ones.
    """
    for hx, where in options:
        if _contrasting(_rgb(primary), _rgb(hx)):
            return hx, where
    return "", ""


def header_backgrounds(css: str) -> list[str]:
    out = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _usable(css)):
        if any(s.strip().lower() in _HEADER_SELECTORS for s in selectors.split(",")):
            m = re.search(r"background(?:-color)?\s*:\s*([^;]+)", body, re.I)
            if m:
                out.append(m.group(1))
    return out


def _first_colour(values: list[str]) -> str:
    for value in values:
        for token in re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", value):
            hx = to_hex(token)
            if hx and hx not in _STOCK and chromatic(_rgb(hx)):
                return hx
    return ""


def _theme_colour(page: _Page) -> str:
    values = [m.get("content", "") for m in page.metas
              if m.get("name", "").lower() == "theme-color" and "dark" not in m.get("media", "").lower()]
    return _first_colour(values)


def _stylesheets(page: _Page, base: str) -> list[str]:
    urls = []
    for link in page.links:
        if "stylesheet" in _rels(link) and link.get("href"):
            url = urljoin(base, link["href"].strip())
            if _same_host(url, base) and not _LIBRARY_CSS.search(urlparse(url).path) and url not in urls:
                urls.append(url)
    return sorted(urls, key=lambda u: 0 if _LIKELY_CSS.search(urlparse(u).path) else 1)


# --------------------------------------------------------------------------
# Together
# --------------------------------------------------------------------------

def read(base: str, html: str, fetch: Fetch) -> Optional[dict]:
    """The brand a homepage shows, or None when there is nothing confident to say.

    `base` is the homepage's own address, after any redirect: their host is
    the one the page came from. `fetch` reads one same-host file, politely, or
    returns None; it is the caller's so the crawl's rules about other people's
    servers live in one place. Every field present is `#RRGGBB` or an absolute
    URL on their host.
    """
    page = _Page()
    try:
        page.feed(html or "")
        page.close()
    except Exception:
        return None

    found: dict[str, str] = {}
    logo = primary = ""
    # Second colours by strength of evidence; which one holds depends on the
    # first, so the choice waits until the first is settled.
    accents: list[tuple[str, str]] = []

    for where, url in logo_candidates(page, base)[:MAX_LOGO_FETCHES]:
        asset = fetch(url)
        if asset is None or not _same_host(asset.url or url, base):
            continue
        got = _logo(asset)
        if got is None:
            continue
        logo = asset.url or url
        found["logo"] = where
        primary, accent = got
        if primary:
            found["primary"] = "logo"
        if accent:
            accents.append((accent, "logo"))
        break

    css = "\n".join(page.styles)
    props = root_vars(css)

    if not primary:
        primary = _theme_colour(page)
        if primary:
            found["primary"] = "theme-color"
    if not primary:
        primary, name = _declared(props, _PRIMARY_VARS)
        if primary:
            found["primary"] = name
    accents += _declared_all(props, _ACCENT_VARS)

    sheets_css = ""
    if not (primary and _second(primary, accents)[0]):
        for url in _stylesheets(page, base)[:MAX_CSS_FETCHES]:
            asset = fetch(url)
            if asset is None or not _same_host(asset.url or url, base):
                continue
            sheets_css += "\n" + asset.body.decode("utf-8", "replace")
        if sheets_css:
            sheet_props = root_vars(sheets_css)
            if not primary:
                primary, name = _declared(sheet_props, _PRIMARY_VARS)
                if primary:
                    found["primary"] = "stylesheet " + name
            accents += [(hx, "stylesheet " + name) for hx, name in _declared_all(sheet_props, _ACCENT_VARS)]

    if not primary:
        primary = _first_colour(page.header_bgs + header_backgrounds(css + sheets_css))
        if primary:
            found["primary"] = "header background"

    # Never a second colour without a first.
    accent, where = _second(primary, accents) if primary else ("", "")
    if accent:
        found["accent"] = where
    if not (logo or primary):
        return None
    return {"logo": logo, "primary": primary, "accent": accent, "source": "scan", "found_in": found}
