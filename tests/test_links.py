"""Start links.

The thing being protected here is that a button which says it will open a
terminal actually opens it. The demo's five buttons were decorative; a vendor who
puts a dead one on their website finds out in front of a customer.

Copilot is the live example. It shipped as supported on the strength of the URL
being well-formed, and Microsoft had already removed prefill after the "Reprompt"
injection attack — so the button opened Copilot with an empty box and the customer
saw nothing wrong until nothing happened.
"""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

import pytest

from a2b import links

PROMPT = "I want to work with Bluebonnet on General C&I.\nAsk: what is the address?"

# Terminals that claim to accept a prompt from a link. Copilot is deliberately
# absent; see the module docstring.
LINKING = ("claude", "chatgpt", "grok")


def by_key(terminals):
    return {t["key"]: t for t in terminals}


def urls(terminals):
    return [t for t in terminals if t["url"]]


def test_every_linking_terminal_has_a_real_url():
    terms = by_key(links.terminal_links(PROMPT))
    assert set(terms) == {"claude", "chatgpt", "grok", "copilot"}
    for key in LINKING:
        t = terms[key]
        assert t["supported"], f"{key} is not marked supported"
        assert t["url"], f"{key} has no URL to open"
        assert t["url"].startswith("https://"), f"{key} URL is not https: {t['url']}"


def test_copilot_ships_as_copy_not_as_a_dead_link():
    """Microsoft removed `?q=` prefill. A link that loads Copilot with an empty
    box is worse than no link, because it looks like it worked."""
    copilot = by_key(links.terminal_links(PROMPT))["copilot"]
    assert copilot["supported"] is False
    assert copilot["url"] is None
    assert "copy" in copilot["note"].lower()


def test_supported_and_url_never_disagree():
    """`supported` means the URL is real. Nothing aspirational ships as if it
    works, in either direction."""
    for t in links.terminal_links(PROMPT):
        assert t["supported"] == bool(t["url"]), f"{t['key']} disagrees with itself"


def test_the_prompt_survives_the_round_trip_intact():
    """The prompt contains newlines, an ampersand and a question mark, all of
    which break a URL if the encoding is wrong. A half-encoded prompt arrives
    truncated at the first `&`, which silently drops the guardrail line."""
    for t in urls(links.terminal_links(PROMPT)):
        q = parse_qs(urlparse(t["url"]).query, keep_blank_values=True)
        assert q["q"][0] == PROMPT, f"{t['key']} mangled the prompt"


def test_claude_is_flagged_as_prefill_only_and_the_others_send():
    terms = by_key(links.terminal_links(PROMPT))
    assert terms["claude"]["autosubmits"] is False
    assert "press enter" in terms["claude"]["note"].lower()
    for key in ("chatgpt", "grok"):
        assert terms[key]["autosubmits"] is True, f"{key} should send on arrival"


def test_grok_tells_the_customer_to_sign_in_first():
    """Reported as "the prompt never appears". A signed-out visitor loses it
    through the login redirect, which is worth saying on the button."""
    assert "signed in" in by_key(links.terminal_links(PROMPT))["grok"]["note"]


def test_claude_desktop_gets_the_custom_scheme():
    terms = by_key(links.terminal_links(PROMPT))
    assert terms["claude"]["desktop_url"].startswith("claude://claude.ai/new?q=")
    # Nobody else publishes one, and inventing schemes produces dead buttons.
    for key in ("chatgpt", "grok", "copilot"):
        assert terms[key]["desktop_url"] is None


def test_a_long_prompt_falls_back_rather_than_truncating():
    """Past the URL ceiling the link must not carry a half prompt. Losing the
    back half means losing the guardrail and the delivery instruction."""
    long_prompt = "Section instructions. " * 500
    fallback = "Open https://api.example.com/v/b/start/x.md and follow it."
    terms = links.terminal_links(long_prompt, fallback_prompt=fallback)

    for t in urls(terms):
        assert t["shortened"] is True
        sent = parse_qs(urlparse(t["url"]).query)["q"][0]
        assert sent == fallback
        assert sent != long_prompt[: len(sent)], "the prompt was truncated, not replaced"


def test_a_full_workflow_prompt_still_fits_in_the_link():
    """The prompt carries every section now, so the ceiling has to clear a real
    intake. Four sections of five questions must not trip the fallback."""
    sections = "\n".join(
        f"{i}. Section {i}\n   Open with: Some framing sentence for this part.\n"
        "   Ask:\n" + "\n".join(f"     - Question {j} about the deal?" for j in range(5))
        for i in range(1, 5)
    )
    terms = links.terminal_links(f"Preamble.\n\n{sections}\n\nGuardrail.",
                                 fallback_prompt="short")
    assert all(t["shortened"] is False for t in terms)


def test_a_normal_prompt_is_not_shortened():
    terms = links.terminal_links(PROMPT, fallback_prompt="short")
    assert all(t["shortened"] is False for t in terms)
    for t in urls(terms):
        assert unquote(urlparse(t["url"]).query) == f"q={PROMPT}"


def test_no_fallback_means_the_full_prompt_goes_out():
    """Without somewhere to point, a short prompt would be worse than a long
    URL — so the full prompt goes out and the caller is told nothing changed."""
    terms = links.terminal_links("x" * 9000)
    assert all(t["shortened"] is False for t in terms)


@pytest.mark.parametrize("website,slug,expected", [
    ("bluebonnetbank.com", "s", "https://bluebonnetbank.com/start/s"),
    ("https://bluebonnetbank.com/", "s", "https://bluebonnetbank.com/start/s"),
    ("", "s", "https://lenni.io/v-slug/start/s"),
])
def test_universal_link_prefers_the_vendors_own_domain(website, slug, expected):
    assert links.universal_link(website, "v-slug", slug) == expected


def test_the_hosted_url_is_absolute_and_ours():
    """What the vendor will put on their site and what answers today are two
    different addresses. Anything an AI is told to fetch must be the second."""
    hosted = links.hosted_start_url("b", "x")
    assert hosted.startswith(("http://", "https://"))
    assert hosted.endswith("/v/b/start/x")
    assert links.hosted_start_url("b", "x", fmt="md") == f"{hosted}.md"


def test_start_links_points_the_fallback_at_a_url_that_answers():
    """The vendor's own domain does not serve our start page until they wire it
    up, so the fallback has to use the hosted one or it points into thin air."""
    out = links.start_links(vendor_website="bank.com", vendor_slug="b",
                            skill_slug="x", entry_prompt="y" * 9000)
    assert out["universal"] == "https://bank.com/start/x"
    assert out["hosted"].endswith("/v/b/start/x")
    assert out["workflow"].endswith("/v/b/start/x.md")
    for t in urls(out["terminals"]):
        sent = unquote(urlparse(t["url"]).query)
        assert "/v/b/start/x" in sent
        assert "bank.com/start/x" not in sent
