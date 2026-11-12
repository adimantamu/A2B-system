#!/usr/bin/env python3
"""Build the GitHub Issues tutorial PDF.

    python scripts/build_issues_guide.py
    -> docs/GitHub_Issues_Guide.pdf

Written for someone who has never used GitHub Issues and is going to be reading
this project's board. The illustrations are drawn rather than screenshotted,
because the repository is private and a screenshot of it could not be reproduced
by anyone re-running this script. They are populated with the board's real
contents, so what the reader sees here is what they will see on the site.

Every string in ILLUSTRATION and BOARD below is real data pulled from the repo.
Refresh it with:

    gh issue list --state open --limit 40 \
        --json number,title,labels --jq '.[] | "\(.number)|..."'
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, KeepTogether,
                                NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "GitHub_Issues_Guide.pdf"

# ---------------------------------------------------------------------------
# Palette. GitHub's own light-theme values, so the drawings read as familiar.
# ---------------------------------------------------------------------------
FG = colors.HexColor("#1f2328")
MUTED = colors.HexColor("#59636e")
LINE = colors.HexColor("#d1d9e0")
SUBTLE = colors.HexColor("#f6f8fa")
BLUE = colors.HexColor("#0969da")
GREEN = colors.HexColor("#1f883d")
PURPLE = colors.HexColor("#8250df")
RED = colors.HexColor("#cf222e")
CALLOUT = colors.HexColor("#bc4c00")
NAVY = colors.HexColor("#1e3a5f")

BODY_FONT, BOLD_FONT, MONO_FONT = "Helvetica", "Helvetica-Bold", "Courier"

PAGE_W, PAGE_H = LETTER
MARGIN = 0.75 * inch
CONTENT_W = PAGE_W - 2 * MARGIN

# Illustrations reserve a strip on the left for their numbered markers so the
# markers never cover the interface they are pointing at.
GUTTER = 26

# WinAnsi has no arrows, ticks or emoji. Anything outside it renders as a black
# box in Helvetica, so the text below stays inside it deliberately.

# ---------------------------------------------------------------------------
# Real data from the board
# ---------------------------------------------------------------------------
LABELS = [
    ("p0-blocker", "b60205", "Blocks deployment"),
    ("p1", "d93f0b", "Needed for a real pilot"),
    ("p2", "fbca04", "Wanted, not urgent"),
    ("done", "0e8a16", "Built and verified"),
    ("epic", "5319e7", "Tracking issue"),
    ("bug", "d73a4a", "Something isn't working"),
    ("area:runtime", "1d76db", "Sessions, chaining, MCP"),
    ("area:infra", "1d76db", "Deployment, storage, auth"),
    ("area:console", "1d76db", "Vendor console"),
    ("area:publish", "1d76db", "Links and the agent manifest"),
    ("area:ingest", "1d76db", "Website scanning and matching"),
    ("area:content", "1d76db", "Registry: products, templates, library"),
    ("area:tenancy", "1d76db", "Vendors, skills, version pinning"),
    ("area:compiler", "1d76db", "Prompt compilation"),
]
LABEL_COLOR = {name: hexc for name, hexc, _ in LABELS}

# number, labels, title
BOARD = [
    (26, ["p1", "area:infra"], "Make it a real public website: domain, TLS, AWS networking"),
    (25, ["p1", "area:infra"], "Rate limiting on the public endpoints"),
    (24, ["p0-blocker", "bug", "area:runtime"],
     "lenni_get_user_context returns any customer profile given its email"),
    (23, ["p2", "area:tenancy"], "Notify vendors when a pinned template version is superseded"),
    (22, ["p2", "area:runtime"], "id-verify (Plaid) app"),
    (21, ["p2", "area:console"], "AI assist for section editing"),
    (20, ["p2", "area:console"], "Funnel analytics: aggregate the events into something readable"),
    (19, ["p2", "area:infra"], "Migrate from SQLite to Postgres"),
    (18, ["p1", "area:publish"], "Establish which terminal deep links actually route"),
    (17, ["p1", "area:ingest"], "Multi-page ingest: crawl beyond a single URL"),
    (16, ["p1", "area:content"], "Expand the product registry to full breadth"),
    (15, ["p1", "area:infra"], "Outbound email: team invites and the email destination"),
    (14, ["p1", "area:runtime"], "Convey delivery integration"),
    (13, ["p1", "area:runtime"], "Port FFIEC bank matching as the fdic-bank-data app"),
    (1, ["epic"], "Epic: A2B system - build status and roadmap"),
]
OPEN_COUNT, CLOSED_COUNT = 15, 11


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def style(name, **kw):
    base = dict(name=name, fontName=BODY_FONT, fontSize=9.5, leading=14,
                textColor=FG, alignment=TA_LEFT, spaceBefore=0, spaceAfter=0)
    base.update(kw)
    return ParagraphStyle(**base)


S = {
    "h1": style("h1", fontName=BOLD_FONT, fontSize=19, leading=23, textColor=NAVY,
                spaceAfter=3),
    "kicker": style("kicker", fontName=BOLD_FONT, fontSize=8, leading=11,
                    textColor=CALLOUT, spaceAfter=5),
    "h2": style("h2", fontName=BOLD_FONT, fontSize=12.5, leading=16, textColor=NAVY,
                spaceBefore=13, spaceAfter=4),
    "h3": style("h3", fontName=BOLD_FONT, fontSize=10, leading=13, textColor=FG,
                spaceBefore=9, spaceAfter=2),
    "body": style("body", spaceAfter=7),
    "lead": style("lead", fontSize=11, leading=16, textColor=MUTED, spaceAfter=9),
    "bullet": style("bullet", leftIndent=13, bulletIndent=2, spaceAfter=4),
    "note": style("note", fontSize=8.5, leading=12, textColor=MUTED, spaceAfter=6),
    "cap": style("cap", fontSize=8, leading=11, textColor=MUTED, spaceBefore=4,
                 spaceAfter=8),
    "mono": style("mono", fontName=MONO_FONT, fontSize=8.5, leading=12.5,
                  textColor=FG, spaceAfter=6),
    "cover_t": style("cover_t", fontName=BOLD_FONT, fontSize=31, leading=36,
                     textColor=colors.white),
    "cover_s": style("cover_s", fontSize=13, leading=19,
                     textColor=colors.HexColor("#c8d6e8")),
    "cover_m": style("cover_m", fontSize=9, leading=14,
                     textColor=colors.HexColor("#8fa6c4")),
}


def P(text, kind="body"):
    return Paragraph(text, S[kind])


def bullets(items, kind="bullet"):
    return [Paragraph(t, S[kind], bulletText="\u2022") for t in items]


def mono(text):
    """Inline code. Paragraph markup, so it can sit inside a sentence."""
    return f'<font face="{MONO_FONT}" size="8.5">{text}</font>'


# ---------------------------------------------------------------------------
# Drawing primitives
# ---------------------------------------------------------------------------
def contrast_on(hexc: str):
    """White or dark text, whichever is readable on the chip. GitHub does this
    too, which is why the near-white 'wontfix' label has dark text."""
    r, g, b = (int(hexc[i:i + 2], 16) for i in (0, 2, 4))
    return colors.white if (0.299 * r + 0.587 * g + 0.114 * b) < 150 else FG


def clip(text: str, font: str, size: float, width: float) -> str:
    """Truncate to fit, with an ellipsis.

    Whole words go first: cutting mid-word gives "AWS networki..." which reads as
    a broken renderer rather than as a title that was too long for the row.
    """
    ell = "\u2026"
    if stringWidth(text, font, size) <= width:
        return text
    words = text.split()
    while len(words) > 1:
        words.pop()
        candidate = " ".join(words).rstrip(",:;-")
        if stringWidth(candidate + ell, font, size) <= width:
            return candidate + ell
    while text and stringWidth(text + ell, font, size) > width:
        text = text[:-1]
    return text + ell


def wrap_lines(text: str, font: str, size: float, width: float) -> list[str]:
    """Greedy word wrap, for text drawn straight onto the canvas."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and stringWidth(trial, font, size) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


class Mock(Flowable):
    """A drawn UI illustration.

    Coordinates run from the top-left with y increasing downward, because that is
    how the layout is described in prose and translating in the head twice is how
    the diagrams end up misaligned.
    """

    def __init__(self, width, height):
        self.width, self.height = width, height

    def wrap(self, *_):
        return self.width, self.height

    def draw(self):
        self.paint()

    # -- helpers -----------------------------------------------------------
    def _y(self, y):
        return self.height - y

    def box(self, x, y, w, h, *, fill=None, stroke=LINE, r=6, lw=0.7):
        c = self.canv
        if fill is not None:
            c.setFillColor(fill)
        if stroke is not None:
            c.setStrokeColor(stroke)
            c.setLineWidth(lw)
        c.roundRect(x, self._y(y + h), w, h, r,
                    stroke=1 if stroke else 0, fill=1 if fill is not None else 0)

    def rect(self, x, y, w, h, *, fill=None, stroke=None, lw=0.7):
        c = self.canv
        if fill is not None:
            c.setFillColor(fill)
        if stroke is not None:
            c.setStrokeColor(stroke)
            c.setLineWidth(lw)
        c.rect(x, self._y(y + h), w, h,
               stroke=1 if stroke else 0, fill=1 if fill is not None else 0)

    def text(self, x, y, s, *, font=BODY_FONT, size=8.5, color=FG, max_w=None):
        if max_w:
            s = clip(s, font, size, max_w)
        c = self.canv
        c.setFont(font, size)
        c.setFillColor(color)
        c.drawString(x, self._y(y + size * 0.78), s)
        return stringWidth(s, font, size)

    def rtext(self, x, y, s, *, font=BODY_FONT, size=8.5, color=MUTED):
        c = self.canv
        c.setFont(font, size)
        c.setFillColor(color)
        c.drawRightString(x, self._y(y + size * 0.78), s)

    def hline(self, x, y, w, color=LINE, lw=0.7):
        c = self.canv
        c.setStrokeColor(color)
        c.setLineWidth(lw)
        c.line(x, self._y(y), x + w, self._y(y))

    def chip(self, x, y, label, *, size=6.8, pad=5, h=13):
        """A label pill in its real colour."""
        hexc = LABEL_COLOR.get(label, "d1d9e0")
        w = stringWidth(label, BOLD_FONT, size) + pad * 2
        fill = colors.HexColor("#" + hexc)
        self.box(x, y, w, h, fill=fill, stroke=fill, r=h / 2)
        self.canv.setFont(BOLD_FONT, size)
        self.canv.setFillColor(contrast_on(hexc))
        self.canv.drawString(x + pad, self._y(y + h / 2 + size * 0.35), label)
        return w

    def state_pill(self, x, y, text, color, *, w=52, h=16):
        self.box(x, y, w, h, fill=color, stroke=color, r=h / 2)
        self.canv.setFont(BOLD_FONT, 7.5)
        self.canv.setFillColor(colors.white)
        self.canv.drawCentredString(x + w / 2, self._y(y + h / 2 + 2.6), text)

    def badge(self, x, y, n, *, r=7.5):
        """A numbered callout marker, keyed to the list under the diagram."""
        c = self.canv
        c.setFillColor(CALLOUT)
        c.setStrokeColor(colors.white)
        c.setLineWidth(1.4)
        c.circle(x, self._y(y), r, stroke=1 if r > 5 else 0, fill=1)
        c.setFont(BOLD_FONT, 8)
        c.setFillColor(colors.white)
        c.drawCentredString(x, self._y(y + 2.9), str(n))

    def gutter_badge(self, n, y, to_x, *, at=None):
        """A badge in the left gutter with an arrow to the row it describes.

        Markers sat directly on their target in the first draft and hid the very
        thing they pointed at - the state pill read as a blank green lozenge.
        """
        gy = y if at is None else at
        self.badge(GUTTER / 2, gy, n)
        self.arrow(GUTTER / 2 + 9, gy, to_x, y)

    def avatar(self, x, y, initials, *, d=16, fill=NAVY):
        c = self.canv
        c.setFillColor(fill)
        c.circle(x + d / 2, self._y(y + d / 2), d / 2, stroke=0, fill=1)
        c.setFont(BOLD_FONT, 6.5)
        c.setFillColor(colors.white)
        c.drawCentredString(x + d / 2, self._y(y + d / 2 + 2.3), initials)

    def arrow(self, x1, y1, x2, y2, *, color=CALLOUT, lw=1.1):
        """A straight connector with a solid head at the far end."""
        c = self.canv
        c.setStrokeColor(color)
        c.setFillColor(color)
        c.setLineWidth(lw)
        c.line(x1, self._y(y1), x2, self._y(y2))
        dx, dy = x2 - x1, -(y2 - y1)
        n = max((dx * dx + dy * dy) ** 0.5, 0.001)
        ux, uy = dx / n, dy / n
        px, py = -uy, ux
        tipx, tipy = x2, self._y(y2)
        c.setLineWidth(0)
        p = c.beginPath()
        p.moveTo(tipx, tipy)
        p.lineTo(tipx - ux * 7 + px * 3.2, tipy - uy * 7 + py * 3.2)
        p.lineTo(tipx - ux * 7 - px * 3.2, tipy - uy * 7 - py * 3.2)
        p.close()
        c.drawPath(p, stroke=0, fill=1)


# ---------------------------------------------------------------------------
# Illustration 1 - a single issue page
# ---------------------------------------------------------------------------
class IssuePage(Mock):
    TITLE = "lenni_get_user_context returns any customer profile"

    def __init__(self, w=CONTENT_W):
        super().__init__(w, 268)

    def paint(self):
        x0 = GUTTER
        W = self.width - GUTTER
        self.box(x0, 0, W, self.height, fill=colors.white, stroke=LINE, r=8)

        side_w = 128
        main_w = W - side_w - 46

        # -- title block
        self.text(x0 + 16, 12, self.TITLE, font=BOLD_FONT, size=12.5)
        self.text(x0 + 16 + stringWidth(self.TITLE, BOLD_FONT, 12.5) + 6, 13,
                  "#24", size=12.5, color=MUTED)
        self.gutter_badge(1, 19, x0 - 3)

        self.state_pill(x0 + 16, 34, "Open", GREEN)
        self.text(x0 + 76, 37, "aditya opened this 2 hours ago  \u00b7  0 comments",
                  size=8, color=MUTED)
        self.gutter_badge(2, 42, x0 - 3)
        self.hline(x0, 58, W)

        # -- the comment that is the issue body
        bx, by = x0 + 16, 70
        self.box(bx, by, main_w, 148, fill=colors.white, stroke=LINE, r=6)
        self.rect(bx + 0.5, by + 0.5, main_w - 1, 21, fill=SUBTLE)
        self.hline(bx, by + 21.5, main_w)
        self.avatar(bx + 7, by + 3, "AR", d=15)
        self.text(bx + 28, by + 6, "aditya", font=BOLD_FONT, size=8)
        self.text(bx + 62, by + 6, "commented 2 hours ago", size=8, color=MUTED)
        self.gutter_badge(3, by + 11, bx - 4)

        lines = [
            ("Split out of #12, where it was called \"the sharpest edge", BODY_FONT),
            ("here\". Console auth is now built and this is not.", BODY_FONT),
            ("", BODY_FONT),
            ("The hole", BOLD_FONT),
            ("lenni_get_user_context takes an email and returns", MONO_FONT),
            ("that person's profile. No credential, no session, no check.", BODY_FONT),
            ("", BODY_FONT),
            ("Acceptance", BOLD_FONT),
            ("\u2022 No unauthenticated caller can retrieve profile facts", BODY_FONT),
            ("\u2022 No caller can tell \"has a profile\" from \"does not\"", BODY_FONT),
            ("\u2022 A test that attempts enumeration and fails", BODY_FONT),
        ]
        y = by + 31
        for txt, font in lines:
            if txt:
                self.text(bx + 11, y, txt, font=font,
                          size=7.5 if font == MONO_FONT else 8, max_w=main_w - 24)
            y += 10.4
        self.gutter_badge(4, by + 100, bx - 4)

        # -- composer, present so the page reads as a conversation
        self.box(bx, by + 158, main_w, 24, fill=SUBTLE, stroke=LINE, r=6)
        self.text(bx + 11, by + 166, "Write a comment\u2026", size=8, color=MUTED)

        # -- sidebar
        sx = self.width - side_w - 14
        self.rect(sx - 16, 58, 0.7, self.height - 58, fill=LINE)
        rows = [
            ("Assignees", ["aditya"], None),
            ("Labels", None, ["p0-blocker", "bug", "area:runtime"]),
            ("Projects", ["A2B build"], None),
            ("Milestone", ["Pilot"], None),
        ]
        y, labels_y = 70, 70
        for i, (head, vals, chips) in enumerate(rows):
            self.text(sx, y, head, font=BOLD_FONT, size=7.5, color=MUTED)
            if chips:
                labels_y = y
            y += 12
            if chips:
                cx, cy = sx, y
                for ch in chips:
                    w = stringWidth(ch, BOLD_FONT, 6.8) + 10
                    if cx + w > sx + side_w:
                        cx, cy = sx, cy + 16
                    cx += self.chip(cx, cy, ch) + 4
                y = cy + 20
            else:
                self.text(sx, y, vals[0], size=8)
                y += 15
            if i < len(rows) - 1:
                self.hline(sx, y - 5, side_w - 4)
                y += 4

        # Marker 5 sits beside the Labels heading. A long arrow up from the foot
        # of the sidebar crossed every row of text on the way.
        self.badge(sx + 56, labels_y + 2, 5)


ISSUE_PAGE_CALLOUTS = [
    ("Title and number", "The number (<b>#24</b>) is permanent and is how everyone "
     "refers to this piece of work in conversation, in commit messages and in other "
     "issues. It is never reused, even after the issue is closed."),
    ("State", "<b>Open</b> or <b>Closed</b>, and nothing in between. If you need "
     "more states than that - in review, blocked, in progress - that is what "
     "Projects are for, in section eleven."),
    ("Who and when", "Every action is attributed and timestamped, automatically. "
     "Nobody has to remember to write a status update for this to be true."),
    ("The body", "Written by whoever opened it, and editable later. Aim for: what "
     "is wrong, why it matters, and how we will know it is fixed. The "
     "<b>Acceptance</b> section at the bottom is that last part."),
    ("Labels, assignee, milestone", "The filing system, down the right-hand side. "
     "Labels are the important one and get a section of their own. An issue with "
     "no labels is invisible to every useful search."),
]


# ---------------------------------------------------------------------------
# Illustration 2 - the issue list
# ---------------------------------------------------------------------------
class IssueList(Mock):
    def __init__(self, w=CONTENT_W, rows=7):
        self.rows = rows
        super().__init__(w, 74 + rows * 30)

    def paint(self):
        x0 = GUTTER
        W = self.width - GUTTER
        self.box(x0, 0, W, self.height, fill=colors.white, stroke=LINE, r=8)

        # -- search bar. The query matches the rows drawn below it, so the
        # illustration is not quietly lying about what the filter did.
        self.box(x0 + 12, 12, W - 24, 24, fill=colors.white, stroke=LINE, r=5)
        self.text(x0 + 22, 20, "is:issue is:open", font=MONO_FONT, size=8.5)
        self.gutter_badge(1, 24, x0 + 9)

        # -- filter row
        self.rect(x0 + 0.5, 44, W - 1, 26, fill=SUBTLE)
        self.hline(x0, 44, W)
        self.hline(x0, 70, W)
        self.text(x0 + 14, 52, f"{OPEN_COUNT} Open", font=BOLD_FONT, size=8.5)
        self.text(x0 + 70, 52, f"{CLOSED_COUNT} Closed", size=8.5, color=MUTED)
        for i, f in enumerate(["Sort", "Assignee", "Milestone", "Label"]):
            self.rtext(self.width - 14 - i * 62, 52, f + "  v", size=8, color=MUTED)
        self.gutter_badge(2, 57, x0 + 9)
        # In the empty middle of the filter row, pointing right at the menus.
        self.badge(self.width - 268, 57, 3)
        self.arrow(self.width - 259, 57, self.width - 236, 57)

        # -- rows
        y = 70
        for num, labs, title in BOARD[:self.rows]:
            c = self.canv
            c.setStrokeColor(GREEN)
            c.setFillColor(colors.white)
            c.setLineWidth(1.3)
            c.circle(x0 + 24, self._y(y + 15), 5, stroke=1, fill=0)
            c.setFillColor(GREEN)
            c.circle(x0 + 24, self._y(y + 15), 1.9, stroke=0, fill=1)

            tw = self.text(x0 + 38, y + 6, title, font=BOLD_FONT, size=8.5,
                           max_w=W - 236)
            cx = x0 + 38 + tw + 6
            for lab in labs:
                if cx + stringWidth(lab, BOLD_FONT, 6.8) + 10 > self.width - 56:
                    break
                cx += self.chip(cx, y + 5, lab) + 4
            self.text(x0 + 38, y + 19, f"#{num} opened 2 hours ago by aditya",
                      size=7.5, color=MUTED)
            self.avatar(self.width - 34, y + 8, "AR", d=15)
            y += 30
            if y < self.height - 2:
                self.hline(x0, y, W)
        self.gutter_badge(4, 85, x0 + 16)


ISSUE_LIST_CALLOUTS = [
    ("The search box is the whole interface", "It is not a text search, it is a "
     "query language. " + mono("is:issue is:open") + " is the default and gives "
     "the list shown here; adding " + mono("label:p0-blocker") + " narrows it to "
     "\"open issues that block deployment\". Section seven has the five queries "
     "worth bookmarking."),
    ("Open and Closed are two separate lists", "Closed issues are not deleted and "
     "not hidden - they are the record of what was done and why. Click "
     "<b>Closed</b> to read the project's history."),
    ("Filter menus write the query for you", "If you would rather not learn the "
     "query syntax, these dropdowns build it. The box updates as you click, which "
     "is a decent way to learn it."),
    ("One row per issue", "The coloured dot means open. The labels sit next to the "
     "title so priority is visible without opening anything - which is the entire "
     "point of labelling consistently."),
]


# ---------------------------------------------------------------------------
# Illustration 3 - lifecycle
# ---------------------------------------------------------------------------
class Lifecycle(Mock):
    def __init__(self, w=CONTENT_W):
        super().__init__(w, 132)

    def paint(self):
        W = self.width
        steps = [
            ("1. Open", "Someone writes\ndown the work", GREEN),
            ("2. Label", "Priority + area,\nso it is findable", BLUE),
            ("3. Discuss", "Comments record\nwhat was decided", CALLOUT),
            ("4. Build", 'Commit says\n"Fixes #24"', PURPLE),
            ("5. Closed", "Auto-closed,\nlinked to the code", MUTED),
        ]
        n = len(steps)
        gap = 20
        bw = (W - gap * (n - 1)) / n
        for i, (head, sub, col) in enumerate(steps):
            x = i * (bw + gap)
            self.box(x, 18, bw, 62, fill=colors.white, stroke=col, r=7, lw=1.3)
            self.rect(x + 0.5, 18.5, bw - 1, 3.5, fill=col)
            self.canv.setFont(BOLD_FONT, 9)
            self.canv.setFillColor(col)
            self.canv.drawCentredString(x + bw / 2, self._y(38), head)
            self.canv.setFont(BODY_FONT, 7.4)
            self.canv.setFillColor(MUTED)
            for j, ln in enumerate(sub.split("\n")):
                self.canv.drawCentredString(x + bw / 2, self._y(52 + j * 10), ln)
            if i < n - 1:
                self.arrow(x + bw + 3, 49, x + bw + gap - 3, 49, color=LINE, lw=1.6)

        self.box(0, 92, W, 38, fill=SUBTLE, stroke=LINE, r=6)
        self.text(14, 101, "The whole point of step 4:", font=BOLD_FONT, size=8.5)
        self.text(14, 114,
                  'writing "Fixes #24" in a commit or pull request closes the '
                  'issue and links the code that fixed it, permanently.',
                  size=8.5, color=MUTED, max_w=W - 28)


# ---------------------------------------------------------------------------
# Illustration 4 - a weak issue beside a strong one
# ---------------------------------------------------------------------------
class GoodBad(Mock):
    TOP, LEAD = 76, 10.4

    def __init__(self, w=CONTENT_W):
        # Sized from the taller column rather than guessed, which is how the
        # first draft ended up clipping its own last line.
        rows = max(len(self.WEAK), len(self.STRONG))
        super().__init__(w, self.TOP + rows * self.LEAD + 12)

    def _panel(self, x, w, *, verdict, vcolor, title, meta, chips, lines):
        self.box(x, 0, w, self.height, fill=colors.white, stroke=vcolor, r=7, lw=1.2)
        self.rect(x + 0.5, 0.5, w - 1, 21, fill=vcolor)
        self.canv.setFont(BOLD_FONT, 8.5)
        self.canv.setFillColor(colors.white)
        self.canv.drawString(x + 10, self._y(15), verdict)

        self.text(x + 10, 30, title, font=BOLD_FONT, size=9, max_w=w - 20)
        if chips:
            cx = x + 10
            for ch in chips:
                cx += self.chip(cx, 44, ch, size=6.2, h=11.5) + 3
            self.text(cx + 2, 45, "\u00b7  assigned", size=7.5, color=MUTED)
        else:
            self.text(x + 10, 45, meta, size=7.5, color=MUTED, max_w=w - 20)
        self.hline(x + 10, 64, w - 20)

        y = self.TOP
        for txt, font in lines:
            if txt:
                self.text(x + 10, y, txt,
                          font={"b": BOLD_FONT, "m": MONO_FONT}.get(font, BODY_FONT),
                          size=7.2 if font == "m" else 7.8,
                          color=MUTED if font == "d" else FG, max_w=w - 20)
            y += self.LEAD

    WEAK = [
        ("it doesnt work when i tried it", "d"),
        ("can someone look", "d"),
        ("", ""),
        ("Why this costs a day:", "b"),
        ("\u2022 Which login? Console or customer?", "d"),
        ("\u2022 Broken how - an error, a blank page,", "d"),
        ("  or a wrong password accepted?", "d"),
        ("\u2022 No labels, so it appears in no search", "d"),
        ("\u2022 Nobody can tell when it is fixed", "d"),
        ("", ""),
        ("Someone has to ask three questions", "d"),
        ("before work can start, and the answers", "d"),
        ("land in chat instead of here.", "d"),
    ]
    STRONG = [
        ("Steps to reproduce", "b"),
        ("1. Log in, then wait past expiry", ""),
        ("2. Reload /console/", ""),
        ("", ""),
        ("Expected: sent back to the login page.", ""),
        ("Actual: still signed in.", ""),
        ("", ""),
        ("Where I think it is", "b"),
        ("auth.user_for_token compares", "m"),
        ("expires_at as a string.", "m"),
        ("", ""),
        ("Acceptance", "b"),
        ("\u2022 An expired cookie is rejected", ""),
        ("\u2022 A test that fails before the fix", ""),
    ]

    def paint(self):
        w = (self.width - 16) / 2
        self._panel(0, w, verdict="Hard to act on", vcolor=RED,
                    title="Login is broken",
                    meta="no labels  \u00b7  no assignee", chips=None,
                    lines=self.WEAK)
        self._panel(w + 16, w, verdict="Ready to pick up", vcolor=GREEN,
                    title="Login accepts an expired session cookie",
                    meta="", chips=["p0-blocker", "bug", "area:infra"],
                    lines=self.STRONG)


# ---------------------------------------------------------------------------
# Illustration 5 - epic with a task list
# ---------------------------------------------------------------------------
class EpicMock(Mock):
    def __init__(self, w=CONTENT_W):
        super().__init__(w, 152)

    def paint(self):
        W = self.width
        self.box(0, 0, W, self.height, fill=colors.white, stroke=LINE, r=8)
        self.text(14, 14, "Epic: A2B system - build status and roadmap",
                  font=BOLD_FONT, size=11)
        self.text(14 + stringWidth("Epic: A2B system - build status and roadmap",
                                   BOLD_FONT, 11) + 6, 15, "#1", size=11, color=MUTED)
        self.chip(14, 34, "epic", size=7, h=14)
        self.hline(0, 56, W)

        # progress
        self.text(14, 66, "Tasks", font=BOLD_FONT, size=8.5)
        self.text(52, 66, "11 of 15 complete", size=8, color=MUTED)
        bar_x, bar_w = 150, W - 164
        self.box(bar_x, 67, bar_w, 7, fill=colors.HexColor("#eaeef2"),
                 stroke=None, r=3.5)
        self.box(bar_x, 67, bar_w * 11 / 15, 7, fill=GREEN, stroke=None, r=3.5)

        items = [
            (True, "#11", "Test suite and console walkthrough"),
            (True, "#12", "Authentication and authorization"),
            (False, "#24", "lenni_get_user_context returns any customer profile"),
            (False, "#26", "Make it a real public website: domain, TLS, AWS"),
        ]
        y = 86
        for done, num, title in items:
            c = self.canv
            self.box(16, y + 1, 10, 10, fill=GREEN if done else colors.white,
                     stroke=GREEN if done else LINE, r=2.5)
            if done:
                c.setStrokeColor(colors.white)
                c.setLineWidth(1.4)
                c.line(18.4, self._y(y + 6.4), 20.4, self._y(y + 8.6))
                c.line(20.4, self._y(y + 8.6), 24, self._y(y + 3.6))
            self.text(33, y + 2, num, font=MONO_FONT, size=7.8, color=BLUE)
            self.text(64, y + 2, title, size=8,
                      color=MUTED if done else FG, max_w=W - 150)
            self.state_pill(W - 66, y - 1, "Closed" if done else "Open",
                            PURPLE if done else GREEN, w=50, h=14)
            y += 15


class ProjectBoard(Mock):
    """The same issues as the list view, arranged as a board.

    Drawn because section eleven otherwise describes columns the reader has
    never seen and asks them to decide whether they want them.
    """

    COLS = [
        ("Todo", [(26, "Make it a real public website", "p1"),
                  (17, "Multi-page ingest: crawl beyond one URL", "p1"),
                  (19, "Migrate from SQLite to Postgres", "p2")]),
        ("In progress", [(24, "lenni_get_user_context returns any profile",
                         "p0-blocker")]),
        ("Done", [(12, "Authentication and authorization", None),
                  (18, "Establish which deep links route", None),
                  (11, "Test suite and console walkthrough", None)]),
    ]

    GAP, TOP, LEAD, PAD = 12, 36, 9.4, 8

    def __init__(self, w=CONTENT_W):
        self.cw = (w - self.GAP * 2) / 3
        # Wrap first, then size each card and the board to what the text needs.
        # Fixed card heights put two-line titles underneath their own label.
        self.laid = []
        for name, cards in self.COLS:
            out = []
            for num, title, pri in cards:
                lines = wrap_lines(title, BODY_FONT, 7.6, self.cw - 34)
                h = 17 + len(lines) * self.LEAD + (14 if pri else 0) + 7
                out.append((num, lines, pri, h))
            self.laid.append((name, out))
        tallest = max(
            self.TOP + sum(h + self.PAD for _, _, _, h in cards)
            for _, cards in self.laid
        )
        super().__init__(w, tallest + 4)

    def paint(self):
        for i, (name, cards) in enumerate(self.laid):
            x = i * (self.cw + self.GAP)
            self.box(x, 0, self.cw, self.height, fill=SUBTLE, stroke=LINE, r=7)
            self.text(x + 10, 10, name, font=BOLD_FONT, size=8.5)
            self.rtext(x + self.cw - 10, 10, str(len(cards)), size=8.5)
            self.hline(x + 8, 26, self.cw - 16)

            y = self.TOP
            for num, lines, pri, h in cards:
                self.box(x + 8, y, self.cw - 16, h, fill=colors.white,
                         stroke=LINE, r=5)
                self.text(x + 16, y + 6, f"#{num}", font=MONO_FONT, size=7,
                          color=MUTED)
                for j, ln in enumerate(lines):
                    self.text(x + 16, y + 17 + j * self.LEAD, ln, size=7.6,
                              color=MUTED if pri is None else FG)
                if pri:
                    self.chip(x + 16, y + 20 + len(lines) * self.LEAD, pri,
                              size=6.2, h=11)
                y += h + self.PAD


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def grid_table(data, widths, *, head=True, size=8.5, pad=6):
    # Plain strings in a Table cell do not wrap, they run off the page. Every
    # body cell becomes a Paragraph so long prose stays inside the margin.
    cell = style("cell", fontSize=size, leading=size * 1.45)
    first_body = 1 if head else 0
    data = [
        [Paragraph(c, cell) if isinstance(c, str) and r >= first_body else c
         for c in row]
        for r, row in enumerate(data)
    ]
    t = Table(data, colWidths=widths, hAlign="LEFT")
    cmds = [
        ("FONT", (0, 0), (-1, -1), BODY_FONT, size),
        ("TEXTCOLOR", (0, 0), (-1, -1), FG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), pad),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, LINE),
        ("BOX", (0, 0), (-1, -1), 0.7, LINE),
    ]
    if head:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), SUBTLE),
            ("FONT", (0, 0), (-1, 0), BOLD_FONT, size),
            ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, LINE),
        ]
    t.setStyle(TableStyle(cmds))
    return t


def callout_list(items):
    """The numbered explanations that pair with a diagram's badges."""
    rows = []
    for i, (head, text) in enumerate(items, 1):
        rows.append([
            Paragraph(f'<font color="#bc4c00"><b>{i}</b></font>', S["body"]),
            Paragraph(f"<b>{head}.</b> {text}", S["body"]),
        ])
    t = Table(rows, colWidths=[16, CONTENT_W - 16], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


class ChipStrip(Mock):
    """The project's real labels, at their real colours. On the cover it doubles
    as a legend: the reader meets them here before section five explains them."""

    SIZE, PAD, H, GAP, LEAD = 7.4, 7, 15, 6, 20

    def __init__(self, names, w=CONTENT_W):
        self.names = names
        # Lay out first, then size to the result - thirteen chips do not fit on
        # one line and a fixed height would let the second row escape the box.
        self.rows, row, x = [], [], 0.0
        for n in names:
            cw = stringWidth(n, BOLD_FONT, self.SIZE) + self.PAD * 2
            if row and x + cw > w:
                self.rows.append(row)
                row, x = [], 0.0
            row.append(n)
            x += cw + self.GAP
        if row:
            self.rows.append(row)
        super().__init__(w, len(self.rows) * self.LEAD + 8)

    def paint(self):
        for i, row in enumerate(self.rows):
            x = 0
            for n in row:
                x += self.chip(x, 4 + i * self.LEAD, n,
                               size=self.SIZE, pad=self.PAD, h=self.H) + self.GAP


class ChipCell(Mock):
    """One label chip, sized to sit inside a table cell."""

    def __init__(self, name, w=72):
        self.name = name
        super().__init__(w, 12)

    def paint(self):
        if self.name and self.name != "-":
            self.chip(0, 0, self.name, size=6.4, pad=5, h=11)


class Rule(Flowable):
    def __init__(self, w=CONTENT_W, color=LINE, thickness=0.7, space=0):
        self.width, self.height = w, thickness + space
        self.color, self.thickness = color, thickness

    def wrap(self, *_):
        return self.width, self.height

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 0, self.width, 0)


class KeyBox(Flowable):
    """A pull-out box for the one sentence on the page that must survive skimming."""

    GAP_AFTER = 12

    def __init__(self, text, w=CONTENT_W, *, label="THE POINT"):
        self.text, self.width, self.label = text, w, label
        self.para = Paragraph(text, style("kb", fontSize=10, leading=14.5,
                                          textColor=NAVY))
        _, h = self.para.wrap(w - 34, 400)
        # The trailing gap is part of the flowable, so the following paragraph
        # cannot end up flush against the border.
        self.box_h = h + 32
        self.height = self.box_h + self.GAP_AFTER

    def wrap(self, *_):
        return self.width, self.height

    def draw(self):
        c = self.canv
        y0 = self.GAP_AFTER
        c.setFillColor(colors.HexColor("#fff8f2"))
        c.setStrokeColor(CALLOUT)
        c.setLineWidth(0.8)
        c.roundRect(0, y0, self.width, self.box_h, 6, stroke=1, fill=1)
        c.setFillColor(CALLOUT)
        c.rect(0, y0, 3.5, self.box_h, stroke=0, fill=1)
        c.setFont(BOLD_FONT, 6.8)
        c.drawString(16, y0 + self.box_h - 13, self.label)
        self.para.drawOn(c, 16, y0 + 12)


# ---------------------------------------------------------------------------
# Page furniture
# ---------------------------------------------------------------------------
_NOW = date.today()
TODAY = _NOW.strftime("%d %B %Y")
# Derived so the example queries stay true whenever the guide is rebuilt.
WEEK_AGO = (_NOW - timedelta(days=7)).isoformat()
MONTH_AGO = (_NOW - timedelta(days=28)).isoformat()


def cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
    canvas.setFillColor(colors.HexColor("#0d9488"))
    canvas.rect(0, PAGE_H - 300, 4.5, 300, stroke=0, fill=1)
    canvas.restoreState()


def chrome(canvas, doc):
    canvas.saveState()
    canvas.setFont(BODY_FONT, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, PAGE_H - MARGIN + 20,
                      "GitHub Issues - a working guide")
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN + 20,
                           "ALL-Dunkin / A2B-system")
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, PAGE_H - MARGIN + 13, PAGE_W - MARGIN, PAGE_H - MARGIN + 13)
    canvas.line(MARGIN, MARGIN - 16, PAGE_W - MARGIN, MARGIN - 16)
    canvas.drawString(MARGIN, MARGIN - 28, TODAY)
    canvas.drawRightString(PAGE_W - MARGIN, MARGIN - 28, str(doc.page - 1))
    canvas.restoreState()


def build():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(OUT), pagesize=LETTER,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="GitHub Issues - a working guide",
        author="Aditya Rajiv", subject="How to use GitHub Issues",
    )
    frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="body",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame], onPage=cover),
        PageTemplate(id="body", frames=[frame], onPage=chrome),
    ])
    doc.build(story())
    return OUT


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------
def story():
    s = []

    # ---- cover ----------------------------------------------------------
    s += [
        Spacer(1, 168),
        P("GitHub Issues", "cover_t"),
        P("A working guide", "cover_t"),
        Spacer(1, 16),
        P("How the A2B project tracks what is being built, what is broken, "
          "and what was decided - and how to read it without being a developer.",
          "cover_s"),
        Spacer(1, 150),
        P("THE LABELS ON THIS BOARD", "cover_m"),
        Spacer(1, 8),
        ChipStrip(["p0-blocker", "p1", "p2", "epic", "bug", "area:runtime",
                   "area:infra", "area:console", "area:publish", "area:ingest",
                   "area:content", "area:tenancy", "area:compiler"]),
        Spacer(1, 44),
        P("ALL-Dunkin / A2B-system", "cover_s"),
        Spacer(1, 6),
        P(f"{OPEN_COUNT} open issues &nbsp;\u00b7&nbsp; {CLOSED_COUNT} closed "
          f"&nbsp;\u00b7&nbsp; {TODAY}", "cover_m"),
        NextPageTemplate("body"),
        PageBreak(),
    ]

    # ---- 1. why ---------------------------------------------------------
    s += [
        P("SECTION ONE", "kicker"),
        P("What an issue actually is", "h1"),
        P("One idea, and the rest of this guide is mechanics.", "lead"),
        KeyBox("An issue is one piece of work with a permanent address and its "
               "own conversation attached. That is all. Its value is not that it "
               "is a to-do item - it is that six months from now you can find out "
               "<b>why</b> something was done, and by whom, without asking anyone."),
        P("Every team already tracks work somewhere: a spreadsheet, a chat "
          "thread, somebody's memory. Those all work until the moment you need "
          "the reasoning behind a decision, at which point the spreadsheet has a "
          "row that says \"done\" and the chat thread has scrolled away.", "body"),
        P("Issues fix that by putting the discussion in the same place as the "
          "task, and never deleting either.", "body"),

        P("What you get that a spreadsheet cannot give you", "h2"),
        grid_table([
            ["", "Spreadsheet or chat", "GitHub Issues"],
            ["Finding it later",
             "Search your memory for keywords",
             "Permanent number: #24, forever"],
            ["Why was this done?",
             "Ask whoever remembers",
             "Read the comments on the issue"],
            ["Is it finished?",
             "Someone updates a cell, or forgets",
             "Closes automatically when the code ships"],
            ["What changed the code?",
             "No connection at all",
             "The commit is linked to the issue"],
            ["Status meeting",
             "Ask each person in turn",
             "One saved search, read in 30 seconds"],
        ], [95, 175, CONTENT_W - 270]),

        P("The habit that matters most", "h2"),
        P("If a decision gets made about a piece of work - in a meeting, in a "
          "hallway, on a call - it goes in a comment on the issue. Not because "
          "of process, but because the person who needs it six months from now "
          "will not have been in that meeting, and will not know to ask.", "body"),
        PageBreak(),
    ]

    # ---- 2. anatomy of an issue -----------------------------------------
    s += [
        P("SECTION TWO", "kicker"),
        P("Anatomy of an issue", "h1"),
        P("This is issue #24 on your board, drawn to scale. Five things to know.",
          "lead"),
        IssuePage(),
        P("A real issue from this project. The numbered markers are explained "
          "below and are not part of the interface.", "cap"),
        callout_list(ISSUE_PAGE_CALLOUTS),
        P("What you can safely ignore", "h2"),
        P("An issue page has more controls than anyone uses. Reactions, "
          "subscribing, pinning, transferring, locking - none of them matter for "
          "reading the board. Four things carry all the meaning: the "
          "<b>title</b>, the <b>labels</b>, whether it is <b>open</b>, and the "
          "<b>comments</b>. If you read only those you will not miss anything "
          "important.", "body"),
        PageBreak(),
    ]

    # ---- 3. anatomy of the list -----------------------------------------
    s += [
        P("SECTION THREE", "kicker"),
        P("Anatomy of the list", "h1"),
        P("The screen you will spend the most time on. It is a search engine, "
          "not a folder.", "lead"),
        IssueList(rows=6),
        P("The Issues tab, showing the top of the real board.", "cap"),
        callout_list(ISSUE_LIST_CALLOUTS),
        KeyBox("If you learn one thing from this guide, learn that the box at the "
               "top is a query. Typing " +
               mono("is:open label:p0-blocker") +
               " answers \"what is stopping us shipping?\" in one line, and you "
               "can bookmark the result."),
        PageBreak(),
    ]

    # ---- 4. the board right now -----------------------------------------
    rank = {"p0-blocker": 0, "p1": 1, "p2": 2, "epic": 3}
    graded = []
    for num, labs, title in BOARD:
        pri = next((l for l in labs if l in rank), "-")
        area = next((l for l in labs if l.startswith("area:")), "-")
        graded.append((rank.get(pri, 9), -num, num, pri, area, title))
    rows = [["#", "Priority", "Area", "What it is"]]
    for _, _, num, pri, area, title in sorted(graded):
        rows.append([str(num), ChipCell(pri, 62), ChipCell(area, 74),
                     Paragraph(title, S["note"])])
    s += [
        P("SECTION FOUR", "kicker"),
        P("Your board, right now", "h1"),
        P(f"All {OPEN_COUNT} open issues, most urgent first. This is the table "
          f"the rest of the guide teaches you to read.", "lead"),
        grid_table(rows, [26, 66, 78, CONTENT_W - 170], size=8, pad=4.5),
        Spacer(1, 10),
        P("Read it top to bottom and the state of the project is visible: one "
          "thing blocks deployment (#24), a cluster of work is needed before a "
          "real pilot, and the rest is wanted but not urgent. The eleven closed "
          "issues are the history and are worth reading once.", "body"),
        PageBreak(),
    ]

    # ---- 5. labels ------------------------------------------------------
    s += [
        P("SECTION FIVE", "kicker"),
        P("Labels: the filing system", "h1"),
        P("Two axes, deliberately. Priority answers \"in what order\", area "
          "answers \"which part of the system\".", "lead"),
        P("Priority - how urgent", "h3"),
        grid_table([
            ["Label", "Means", "In practice"],
            [ChipCell("p0-blocker", 66), "Blocks deployment",
             "Drop other things. There is one right now: #24."],
            [ChipCell("p1", 66), "Needed for a real pilot",
             "Must be done before a customer sees this."],
            [ChipCell("p2", 66), "Wanted, not urgent",
             "Real work, no date. Safe to leave alone."],
            [ChipCell("epic", 66), "Tracking issue",
             "Not work itself - a summary of other issues."],
        ], [82, 132, CONTENT_W - 214]),
        Spacer(1, 8),
        P("Area - which part of the system", "h3"),
        P("All eight are the same blue on purpose: they are categories, not "
          "priorities. Colour is reserved for urgency, so a glance at the board "
          "reads as urgency rather than as decoration.", "note"),
        grid_table([
            [ChipCell(n, 78), Paragraph(d, S["note"])]
            for n, _, d in LABELS if n.startswith("area:")
        ], [92, CONTENT_W - 92], head=False, size=8, pad=4),

        P("Why this is worth being strict about", "h2"),
        P("An issue with no labels does not show up in any of the searches in "
          "section seven. It is not lost exactly, but nobody will see it again "
          "unless they scroll past it. Two labels - one priority, one area - "
          "takes three seconds and is the difference between a board and a pile.",
          "body"),
        KeyBox("Every issue gets exactly one priority label and one area label. "
               "Extras like " + mono("bug") + " are fine on top; those two are "
               "the ones that make it findable."),
        PageBreak(),
    ]

    # ---- 6. writing a good issue ----------------------------------------
    s += [
        P("SECTION SIX", "kicker"),
        P("Writing one worth reading", "h1"),
        P("The same problem, filed two ways. The difference is about ninety "
          "seconds of writing.", "lead"),
        GoodBad(),
        P("Both describe a real bug. Only one can be picked up without a "
          "conversation first.", "cap"),
        P("The three questions to answer", "h2"),
        grid_table([
            ["Question", "Why it matters"],
            ["What is wrong?",
             "Specifically, and in the title. \"Login is broken\" could be six "
             "different bugs; the title should say which."],
            ["Why does it matter?",
             "Whoever prioritises this is not necessarily the person who found "
             "it. Say what breaks and for whom."],
            ["How will we know it is fixed?",
             "This is the one people skip, and the one that prevents an issue "
             "sitting open for a month because nobody agreed what done meant."],
        ], [128, CONTENT_W - 128]),
        Spacer(1, 8),
        P("You are not expected to know the technical cause. \"Clicking the "
          "Claude button does nothing\" is an excellent issue - it says what was "
          "clicked and what failed to happen, which is everything needed to start.",
          "body"),
        PageBreak(),
    ]

    # ---- 7. searches ----------------------------------------------------
    s += [
        P("SECTION SEVEN", "kicker"),
        P("Five searches worth bookmarking", "h1"),
        P("Paste into the search box on the Issues tab, then bookmark the "
          "resulting page.", "lead"),
        grid_table([
            ["Question", "Query"],
            ["What is blocking us right now?",
             Paragraph(mono("is:open label:p0-blocker"), S["note"])],
            ["What must happen before a pilot?",
             Paragraph(mono("is:open label:p1"), S["note"])],
            ["What shipped in the last week?",
             Paragraph(mono(f"is:closed closed:&gt;{WEEK_AGO}"), S["note"])],
            ["What is nobody working on?",
             Paragraph(mono("is:open no:assignee"), S["note"])],
            ["What has gone quiet?",
             Paragraph(mono(f"is:open updated:&lt;{MONTH_AGO}"), S["note"])],
        ], [232, CONTENT_W - 232]),

        P("How to run a status check without interrupting anyone", "h2"),
        P("Open the first two bookmarks. The first tells you whether anything is "
          "on fire; the second tells you how far the pilot is. If an issue looks "
          "stalled, read its comments before asking - the answer is usually "
          "already there, which is the whole reason for writing them.", "body"),

        P("A few more pieces of syntax", "h2"),
        grid_table([
            [Paragraph(mono("is:open"), S["note"]), Paragraph("Not yet finished", S["note"])],
            [Paragraph(mono("is:closed"), S["note"]), Paragraph("Finished, kept as record", S["note"])],
            [Paragraph(mono("label:p1"), S["note"]), Paragraph("Has that label", S["note"])],
            [Paragraph(mono("-label:p2"), S["note"]), Paragraph("Does <b>not</b> have it - note the minus", S["note"])],
            [Paragraph(mono("assignee:aditya"), S["note"]), Paragraph("Assigned to a person", S["note"])],
            [Paragraph(mono("no:label"), S["note"]), Paragraph("Never filed properly - worth checking", S["note"])],
            [Paragraph(mono("sort:updated-desc"), S["note"]), Paragraph("Most recently touched first", S["note"])],
        ], [140, CONTENT_W - 140], head=False, size=8, pad=4),
        Spacer(1, 6),
        P("Terms combine with spaces, and spaces mean \"and\". So " +
          mono("is:open label:p1 no:assignee") + " is \"needed for the pilot and "
          "nobody has picked it up\" - which is usually the most useful question "
          "a manager can ask.", "body"),
        PageBreak(),
    ]

    # ---- 8. comments ----------------------------------------------------
    s += [
        P("SECTION EIGHT", "kicker"),
        P("Comments are the decision log", "h1"),
        P("The part that pays for itself, and the part teams skip.", "lead"),
        P("When something is learned about a piece of work, it goes in a comment "
          "rather than replacing the original text. The original stays wrong on "
          "purpose - the sequence of what was believed, then discovered, then "
          "decided is the useful part.", "body"),
        P("A real example from this board", "h2"),
        P("Issue #12 asked for authentication. It was written when nothing was "
          "deployed and it argued that shipping half-built authentication would "
          "be worse than shipping none. Months later the work was done - but not "
          "all of it. Rather than quietly closing it, a comment recorded what was "
          "built, which two decisions were made and why, and which two pieces of "
          "the original scope were <b>not</b> done. Those two became #24 and #25.",
          "body"),
        KeyBox("Because that comment exists, nobody has to wonder whether the "
               "missing pieces were forgotten or deferred on purpose. That "
               "distinction is invisible in a spreadsheet and it is the most "
               "expensive thing to get wrong."),
        P("What is worth a comment", "h2"),
        *bullets([
            "A decision, and the reason for it - especially rejecting the obvious "
            "approach, because the next person will suggest it again.",
            "Something that turned out to be wrong. #18 was written believing "
            "Claude could not accept a prefilled prompt; that had changed. The "
            "comment says so, so the belief does not outlive the fact.",
            "Scope that will not be done, and where it went instead.",
            "What was verified, and what was not. If you tested it in one browser "
            "and not another, say which.",
        ]),
        P("What is not", "h3"),
        P("\"Any update?\" - the timeline already shows when it was last touched. "
          "If it has gone quiet, ask the person directly and put the answer in a "
          "comment.", "note"),
        PageBreak(),
    ]

    # ---- 9. lifecycle ---------------------------------------------------
    s += [
        P("SECTION NINE", "kicker"),
        P("From opened to closed", "h1"),
        P("What happens to an issue, and the one bit of magic worth knowing.",
          "lead"),
        Lifecycle(),
        Spacer(1, 12),
        P("Closing is not deleting", "h2"),
        P("A closed issue stays readable forever, keeps its number, and keeps its "
          "comments. Closing means \"no longer needs attention\", not \"gone\". "
          "The eleven closed issues on this board are the project's history, and "
          "reading them is the fastest way for someone new to understand how it "
          "got here.", "body"),
        P("Linking code to the reason for it", "h2"),
        P("When the work is done, whoever did it writes " + mono("Fixes #24") +
          " in the commit or pull request. GitHub then closes the issue and links "
          "the two permanently. A year later, anyone looking at that code can get "
          "from a confusing line to the discussion that produced it in two clicks.",
          "body"),
        P("This is the single highest-value habit in this guide and it costs eight "
          "characters.", "note"),
        P("If it turns out not to be a real problem", "h2"),
        P("Close it with a comment saying why, or label it " + mono("wontfix") +
          ". Both are better than leaving it open, because an open issue nobody "
          "intends to do makes every count on the board a lie.", "body"),
        PageBreak(),
    ]

    # ---- 10. epics ------------------------------------------------------
    s += [
        P("SECTION TEN", "kicker"),
        P("Epics: one issue that watches the others", "h1"),
        P("When a project has more issues than fit in your head.", "lead"),
        EpicMock(),
        P("Issue #1 on this board. Writing #24 inside a checklist turns it into a "
          "live row: the title, the state and the progress bar all update "
          "themselves.", "cap"),
        P("An epic is a normal issue that holds a checklist of other issues. Its "
          "job is to be the one link you send someone who asks \"how is the "
          "project going?\". It does not track work of its own.", "body"),
        P("Why it is worth the effort", "h2"),
        *bullets([
            "The progress bar is computed, not maintained. Nobody updates it and "
            "so nobody can forget to.",
            "It gives newcomers a front door. #1 on this board opens with what "
            "A2B is, then what is built, then what is not.",
            "It survives people leaving. The reading order lives in the issue "
            "rather than in whoever has been here longest.",
        ]),
        KeyBox("If you read exactly one thing on this repository, make it issue "
               "#1. It is written to be that thing."),
        PageBreak(),
    ]

    # ---- 11. projects ---------------------------------------------------
    s += [
        P("SECTION ELEVEN", "kicker"),
        P("Projects, and when you need one", "h1"),
        P("Issues have two states. Real work has more. That gap is what Projects "
          "fill.", "lead"),
        P("A Project is a board or table view built on top of the same issues - "
          "the classic columns of Todo, In progress, Done. Nothing is "
          "duplicated: an issue on a project board is the same issue, and moving "
          "the card does not change the issue.", "body"),
        ProjectBoard(),
        P("Issues from this project, arranged as a board. Compare it with section "
          "three: identical issues, different question answered.", "cap"),
        grid_table([
            ["Use", "When"],
            ["The Issues list",
             "You want to know what exists and how urgent it is. This is enough "
             "for most of what this project needs today."],
            ["A Project board",
             "You care about flow - what is in progress right now, what is "
             "waiting on review, what is stuck. Useful with several people "
             "working at once."],
        ], [110, CONTENT_W - 110]),
        Spacer(1, 8),
        P("The honest recommendation for this project: labels and saved searches "
          "are doing the job. Add a Project when more than one person is working "
          "at a time and \"who is on what this week\" stops being obvious - not "
          "before, because a board nobody drags cards on is worse than no board, "
          "since it looks authoritative while being stale.", "body"),
        P("Milestones", "h2"),
        P("A milestone is a named bucket with an optional date - \"Pilot\", "
          "\"v1\". It gives you a percentage-complete for a release. Worth adding "
          "when there is a date to hit; noise before that.", "body"),
        PageBreak(),
    ]

    # ---- 12. cheat sheet ------------------------------------------------
    s += [
        P("SECTION TWELVE", "kicker"),
        P("One-page cheat sheet", "h1"),
        P("The whole guide, for pinning up.", "lead"),

        P("Opening an issue", "h3"),
        *bullets([
            "Title says what is wrong, specifically.",
            "Body: what is wrong, why it matters, how we will know it is fixed.",
            "One priority label and one area label. Always.",
            "Assign it if you know who; leave it unassigned if you do not - "
            + mono("no:assignee") + " is a useful search.",
        ]),

        P("Working on one", "h3"),
        *bullets([
            "Decisions go in comments, not in edits to the original text.",
            "Write " + mono("Fixes #24") + " in the commit or pull request.",
            "If scope drops out, open a new issue and say so in a comment.",
        ]),

        P("Reading the board", "h3"),
        *bullets([
            mono("is:open label:p0-blocker") + " - is anything on fire?",
            mono("is:open label:p1") + " - how far is the pilot?",
            mono("is:open no:assignee") + " - what has nobody picked up?",
            "Read the comments before asking for an update.",
        ]),

        P("Keyboard shortcuts on github.com", "h3"),
        grid_table([
            [Paragraph(mono("g i"), S["note"]), Paragraph("Go to Issues", S["note"])],
            [Paragraph(mono("c"), S["note"]), Paragraph("Create a new issue", S["note"])],
            [Paragraph(mono("/"), S["note"]), Paragraph("Jump to the search box", S["note"])],
            [Paragraph(mono("l"), S["note"]), Paragraph("Edit labels on an open issue", S["note"])],
            [Paragraph(mono("?"), S["note"]), Paragraph("Show every shortcut", S["note"])],
        ], [70, CONTENT_W - 70], head=False, size=8, pad=3.5),

        P("Vocabulary", "h3"),
        grid_table([
            [Paragraph("<b>Issue</b>", S["note"]),
             Paragraph("One piece of work, with a number and a conversation.", S["note"])],
            [Paragraph("<b>Label</b>", S["note"]),
             Paragraph("A coloured tag. Here: one priority plus one area.", S["note"])],
            [Paragraph("<b>Epic</b>", S["note"]),
             Paragraph("An issue holding a checklist of other issues.", S["note"])],
            [Paragraph("<b>Milestone</b>", S["note"]),
             Paragraph("A named bucket of issues, usually with a date.", S["note"])],
            [Paragraph("<b>Pull request</b>", S["note"]),
             Paragraph("Proposed code changes, reviewed before merging. Often "
                       "says <i>Fixes #24</i>.", S["note"])],
            [Paragraph("<b>Commit</b>", S["note"]),
             Paragraph("One saved change to the code, with a message.", S["note"])],
            [Paragraph("<b>Repository</b>", S["note"]),
             Paragraph("The project: its code, its issues, its history.", S["note"])],
        ], [86, CONTENT_W - 86], head=False, size=8, pad=4),

        Spacer(1, 14),
        Rule(),
        Spacer(1, 8),
        P(f"Generated from the live board on {TODAY}. Rebuild with "
          + mono("python scripts/build_issues_guide.py") + ".", "note"),
    ]
    return s


if __name__ == "__main__":
    path = build()
    print(f"wrote {path}  ({path.stat().st_size / 1024:.0f} KB)")
