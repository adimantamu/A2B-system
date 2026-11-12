"""Start links — the human door into a skill.

Publishing produces two doors (rule 4): this one for people, and the agent
manifest for AIs. Both reach the same skill and the same destination.

A note on the deep links, because the brief flags this as unsettled and the
situation has moved.

All four terminals below accept the prompt in a `?q=` parameter and open with it
loaded. They differ in one way that matters to the customer, so it is modelled
rather than glossed: ChatGPT, Grok and Copilot **send** the prompt on arrival,
whereas Claude **fills the composer and waits** for the customer to press enter.
Anthropic removed auto-submit from `claude.ai/new?q=` in late 2025; prefill still
works. A button that says "Start in Claude" and lands the customer on a filled-in
box they then have to send is fine — one that claims it will start on its own and
does not is what we are avoiding.

Claude Desktop additionally has an official `claude://` scheme, so `desktop_url`
carries it where one exists.

Two guards worth knowing about:

* **Length.** A `?q=` link is a URL, and URLs have practical limits. The entry
  prompt now carries the vendor's whole intake, which puts a real workflow within
  sight of `MAX_QUERY` rather than nowhere near it — see the note there. Past the
  ceiling the link falls back to a prompt that points the AI at the start page,
  where the full text is offered for copying, rather than truncating mid-sentence.
* **`supported`** still means the URL is real. Nothing aspirational ships as if
  it works.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from .config import PUBLIC_BASE

# Where this number comes from, since it is now load-bearing rather than
# theoretical. Browsers are not the constraint: Chrome, Safari and Firefox all
# take tens of thousands of characters. The constraint is the receiving end,
# where a request line over roughly 8KB is refused by common front-end defaults
# before the application ever sees it — and that refusal looks to the customer
# like the button is broken.
#
# The measurement that matters: a four-section bank intake compiles to about
# 4,800 characters, which is ~7,100 once percent-encoded, so encoding costs about
# half again on top. That fits here with room to spare; a six-section intake
# would not, and would take the fallback below. If vendors start routinely
# building intakes that long, the answer is the connector rather than a bigger
# number, because at that size the opening message has stopped being readable to
# the customer as well.
MAX_QUERY = 8000


@dataclass(frozen=True)
class Terminal:
    key: str
    name: str
    template: Optional[str]
    supported: bool
    # Whether arriving at the URL sends the prompt, or only loads it into the box.
    autosubmits: bool = True
    desktop: Optional[str] = None
    note: str = ""


TERMINALS: tuple[Terminal, ...] = (
    Terminal("claude", "Claude", "https://claude.ai/new?q={prompt}", True,
             autosubmits=False,
             desktop="claude://claude.ai/new?q={prompt}",
             note="Opens a new chat with the prompt filled in. Press enter to send it — "
                  "Claude no longer submits prefilled prompts on its own."),
    Terminal("chatgpt", "ChatGPT", "https://chatgpt.com/?q={prompt}", True,
             note="Opens and sends the prompt."),
    Terminal("grok", "Grok", "https://grok.com/?q={prompt}", True,
             note="Opens and sends the prompt. You need to be signed in to grok.com "
                  "first — a signed-out visit loses the prompt through the login "
                  "redirect, and Grok sometimes shows a Cloudflare check on the way in."),
    # Microsoft removed prefill after the "Reprompt" attack, which used this exact
    # parameter to inject prompts and exfiltrate data; patched January 2026. The
    # URL still loads Copilot and shows the text as a heading, which is worse than
    # an honest failure because it looks like it worked. No documented replacement,
    # so this ships as copy-the-prompt until there is one.
    Terminal("copilot", "Copilot", None, False,
             note="Copilot no longer accepts a prompt from a link. Copy the prompt "
                  "and paste it into a new Copilot chat."),
)


def universal_link(vendor_website: str, vendor_slug: str, skill_slug: str) -> str:
    """The one link a vendor puts on their service page.

    Prefers the vendor's own domain — the whole pitch is that the button lives on
    their site and looks like theirs. Falls back to a Lenni-hosted path when the
    vendor has not told us their domain.

    Note that this is where the button *will* live once the vendor wires it up,
    which is not the same as somewhere that answers today. Anything an AI is told
    to fetch must use `hosted_start_url` instead.
    """
    host = (vendor_website or "").strip().rstrip("/")
    host = host.replace("https://", "").replace("http://", "")
    if host:
        return f"https://{host}/start/{skill_slug}"
    return f"https://lenni.io/{vendor_slug}/start/{skill_slug}"


def hosted_start_url(vendor_slug: str, skill_slug: str, *, fmt: str = "") -> str:
    """The start page *this deployment* serves, which is the one that answers.

    `fmt="md"` gives the plain-text workflow, which is what we point agents at:
    an AI asked to read a page does better with markdown than with a page of
    buttons meant for a person.
    """
    suffix = f".{fmt}" if fmt else ""
    return f"{PUBLIC_BASE}/v/{vendor_slug}/start/{skill_slug}{suffix}"


def section_url(vendor_slug: str, skill_slug: str, position: int) -> str:
    """One section of the intake, addressed by its position.

    The chain behind `staged` mode: the entry prompt carries section 1 and this
    address for section 2, and each page then names the next. Still served for
    every skill regardless of mode, because a customer's assistant that lost its
    place mid-conversation can be pointed at one of these.
    """
    return f"{PUBLIC_BASE}/v/{vendor_slug}/start/{skill_slug}/{position}.md"


def skill_doc_url(vendor_slug: str, skill_slug: str, name: str) -> str:
    """Where one product's agent-readable docs answer.

    Per product rather than per vendor. A customer clicked a loan product, not a
    bank, and an assistant handed the whole vendor manifest has to guess which of
    six services the conversation is about — which it does confidently and
    sometimes wrongly.
    """
    return f"{PUBLIC_BASE}/v/{vendor_slug}/{skill_slug}/{name}"


def short_prompt(universal_url: str) -> str:
    """Last-resort fallback when there is no compiled compact prompt to use."""
    return (f"Open {universal_url} and follow the instructions there to prepare "
            f"my application, section by section.")


def terminal_links(entry_prompt: str, universal_url: str = "",
                   fallback_prompt: str = "") -> list[dict]:
    encoded = quote(entry_prompt, safe="")
    truncating = len(encoded) > MAX_QUERY
    shortened = False
    if truncating and (fallback_prompt or universal_url):
        encoded = quote(fallback_prompt or short_prompt(universal_url), safe="")
        shortened = True

    out = []
    for t in TERMINALS:
        usable = bool(t.template and t.supported)
        out.append({
            "key": t.key,
            "name": t.name,
            "url": t.template.format(prompt=encoded) if usable else None,
            "desktop_url": t.desktop.format(prompt=encoded) if (usable and t.desktop) else None,
            "supported": t.supported,
            "autosubmits": t.autosubmits,
            "note": t.note,
            # Lets the console tell the vendor why the link is not the full prompt.
            "shortened": shortened,
        })
    return out


def start_links(*, vendor_website: str, vendor_slug: str, skill_slug: str,
                entry_prompt: str, fallback_prompt: str = "") -> dict:
    return {
        "universal": universal_link(vendor_website, vendor_slug, skill_slug),
        "hosted": hosted_start_url(vendor_slug, skill_slug),
        "workflow": hosted_start_url(vendor_slug, skill_slug, fmt="md"),
        "terminals": terminal_links(
            entry_prompt,
            universal_url=hosted_start_url(vendor_slug, skill_slug),
            fallback_prompt=fallback_prompt,
        ),
    }
