"""The static pages, checked for the mistakes a browser makes no noise about.

Neither page is compiled or type-checked by anything, so a duplicated attribute
or a renamed element is found by a person clicking on it — in front of whoever
they were demonstrating to, in the case this file exists because of.
"""
from __future__ import annotations

import re
from collections import Counter

import pytest

from a2b.config import ADMIN_DIR, CONSOLE_DIR

PAGES = [CONSOLE_DIR / "index.html", ADMIN_DIR / "index.html"]

_ID = re.compile(r'\bid="([A-Za-z0-9_-]+)"')


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.parent.name)
def test_no_element_id_is_used_twice(page):
    """Two elements sharing an id is silent, and the silence is the problem.

    `getElementById` returns the first match in document order and reports no
    error, so the second element simply stops being addressable. The scanner's
    URL box was the second `scan-url` on the page, behind a display div of the
    same name: reading `.value` off a div gives `undefined`, so a field with a
    URL in it reported itself empty and refused to scan.

    What made it expensive was the half that still worked. Picking a bank from
    the shortlist set `.value` on that same div — legal on any object — and read
    it straight back, so one path worked, one path did not, and the difference
    pointed nowhere near a duplicated attribute.
    """
    ids = _ID.findall(page.read_text(encoding="utf-8"))
    dupes = sorted(name for name, n in Counter(ids).items() if n > 1)
    assert not dupes, f"{page.name} uses these ids more than once: {dupes}"


_HANDLER = re.compile(r'\bon(?:click|change|submit|input)="([^"]*)"')
_CALL = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
# Everything a handler may legitimately reach for that is not ours to define.
_BUILTIN = {
    "alert", "confirm", "prompt", "fetch", "parseInt", "parseFloat", "Number",
    "String", "Boolean", "Array", "Object", "JSON", "Math", "Date", "encodeURIComponent",
    "decodeURIComponent", "setTimeout", "clearTimeout", "if", "for", "while",
    "switch", "return", "function", "catch", "typeof",
}


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.parent.name)
def test_every_inline_handler_points_at_a_function_that_exists(page):
    """`onclick="gone()"` is a dead button and a silent one.

    Nothing links these two files, so a function can be renamed or deleted
    while the markup still cheerfully asks for it. The browser throws only when
    someone clicks, and only into a console nobody has open. This caught the
    sign-in refactor: moving login to its own page removed `logout()`, and four
    handlers were still calling it.
    """
    html = page.read_text(encoding="utf-8")
    js = (page.parent / "app.js").read_text(encoding="utf-8")

    called = {name for body in _HANDLER.findall(html) for name in _CALL.findall(body)}
    defined = set(re.findall(r"(?:async\s+)?function\s+([\w$]+)\s*\(", js))
    defined |= set(re.findall(r"(?:const|let|var)\s+([\w$]+)\s*=\s*(?:async\s*)?(?:\(|function)", js))

    missing = sorted(called - defined - _BUILTIN)
    assert not missing, f"{page.parent.name}/index.html calls undefined: {missing}"


def test_the_scanner_reads_the_box_the_page_actually_shows():
    """The specific wiring above, pinned by name.

    The duplicate check would not have caught the rename on its own: renaming the
    input and forgetting the script leaves every id unique and the button dead.
    """
    html = (CONSOLE_DIR / "index.html").read_text(encoding="utf-8")
    js = (CONSOLE_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="scan-site-url"' in html, "the scanner's input is not where the script looks"
    assert "getElementById('scan-site-url')" in js
    assert "getElementById('scan-url').value" not in js, (
        "reading .value off the display div is the original bug"
    )
