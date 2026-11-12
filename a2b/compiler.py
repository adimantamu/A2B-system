"""Compiles skill configuration into the prompts that carry it.

Two prompts come out of here:

* The **entry prompt** — what a start link injects into the customer's AI. Fully
  compiled, no editable parts. Vendors never hand-write it, which is the only
  reason it stays correct when they reorder sections.
* The **final prompt** — what the AI says at the finish. Here the vendor's voice
  is editable but the wiring is not: the produced-outputs sentence and the
  delivery link are compiled from the destination settings and cannot be typed
  over. Editable voice, locked wiring.

That split is the template-to-your-version rule in miniature, and it is why a
vendor can never accidentally promise a PDF they turned off, or paste a stale
upload link into their closing message.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .config import GUARDRAIL, MCP_BASE

DEFAULT_DESTINATION: dict[str, Any] = {
    "outputs": {"pdf": True, "md": True, "doclist": True},
    "mode": "convey",
    "connected": False,
    "link": "",
    "email": "",
    "texts": {"open": None, "close": None, "deliver": {"convey": None, "link": None, "email": None}},
}

FRESH_CHAT_NOTE = (
    "Before we start: if you have been through this product with me in an earlier "
    "chat, please disregard those previous conversations and work only from this "
    "one. Old answers and an older version of these questions can carry over "
    "without either of us noticing."
)
"""Shown to the customer as the first section is asked.

Earned the hard way. A customer who reopened a previous chat for the same
product got a blend of the two: answers they had given days earlier, and
questions from a version of the intake the bank had since edited. The assistant
did not flag it, because from inside the conversation there is nothing to
notice — it is all just context.

Addressed to the customer rather than the assistant because the assistant cannot
fix it. By the time the old chat is open the stale context is already loaded, and
no instruction in the newer message reliably outranks it.
"""

PDF_TRIGGER = "convert this to PDF"
"""The words the customer types to turn the finished intake into a document.

A fixed phrase rather than a judgement call. The assistant is told to name it,
so the customer is never left wondering whether anything else happens, and it is
unambiguous enough that a paraphrase still lands.
"""

REVIEW_THEN_PDF = (
    f"Please review everything. When it is correct, type: {PDF_TRIGGER}"
)
"""How the summary ends when the whole intake travelled in the opening prompt."""

SUMMARY_OFFER = (
    "Shall I summarize everything you've given me so you can check it?"
)
"""The first of the two beats that end an intake.

Dumping four sections back unannounced buries the question in a wall of text
nobody reads to the bottom of. Asking first is a smaller thing to answer, and it
puts the customer in the position of having requested the summary rather than
being handed one — which is the difference between checking it and skimming it.
"""

PDF_OFFER = (
    "If all the details are entered correctly, tell me to make a PDF and I'll "
    "create one instantly."
)
"""The last line of every completed intake, wherever the assistant read it.

A read-back that ends on a list of missing documents reads as homework, and the
customer never learns a document was one sentence away. One string rather than
three phrasings so the guide, the section chain and the whole-workflow page all
close the same way — and so a customer who lands on the fallback copy is not
offered less than one who read the guide.
"""

WORK_FROM_THE_PAGE = (
    "**Always refer to this context file and answer from it. Do not make up your "
    "own questions or answers — this is extremely crucial for a smooth workflow.**"
)
"""The line immediately before the questions start.

An assistant that has seen a thousand loan intakes will supply the question it
expects rather than the one the vendor wrote, and it will sound right doing it.
Placed here rather than in the preamble because this is the last thing read
before the first question, and the instruction has to be closer to the moment
than the general pacing rules are.
"""

SECTION_CHECK = (
    "Please review everything and if it is correct please say next section, else "
    "tell me what to correct."
)
"""The handover between sections, asked the same way every time.

An assistant that reads a capture back and then keeps talking has decided on the
customer's behalf that it heard them right. Making the advance depend on the
customer saying so turns each section into a checkpoint, which is also what makes
the final package something they have already approved four times over.
"""

ENTRY_TEXT_KEYS = ("fresh", "pacing", "section_check", "summary", "pdf")
"""The paragraphs of the entry prompt a vendor may replace with their own.

Not in here, on purpose: the questions, which are edited as sections and carry
their own checks, and the guardrail, whose absence is a compliance problem rather
than a style choice.
"""


def entry_prompt_defaults(total: int) -> dict[str, str]:
    """Our wording for each editable paragraph.

    Keyed rather than positional, so a vendor's saved text survives us reordering
    or rewriting the blocks around it. A key absent from their overrides means
    they never touched that paragraph and still receive improvements to it; a key
    present means the paragraph is theirs and we leave it alone.
    """
    return {
        "fresh": (
            "Everything you need is here — disregard anything you remember about "
            "this product from an earlier chat, as these are the current "
            "questions. Open section 1 with this line, word for word:\n"
            f"{FRESH_CHAT_NOTE}"
        ),
        "pacing": (
            f"Ask me the {total} sections step by step, one at a time, in their "
            "wording rather than your own. Wait for my answers before moving on, "
            "and do not show me a later section before we reach it — you can see "
            "the whole intake above, and I cannot answer it all at once. Keep a "
            "running record of my progress."
        ),
        "section_check": (
            "After each section, read back what you captured and end with this "
            f"line:\n{SECTION_CHECK}"
        ),
        "summary": (
            "When every section is answered, summarise the whole package back to "
            "me — each section with what you captured, then anything still "
            f"outstanding — and end with this line:\n{REVIEW_THEN_PDF}"
        ),
        "pdf": (
            f'When I type "{PDF_TRIGGER}", turn the whole package into a formatted '
            "PDF I can download, with the outstanding items listed in it. Do not "
            "produce it before I ask, and if you cannot create files in this chat, "
            "say so plainly and print the same document as text instead."
        ),
    }


def _block(overrides: Optional[dict], defaults: dict[str, str], key: str) -> str:
    """One paragraph: the vendor's if they wrote one, ours otherwise.

    Blank counts as "not set" rather than "delete this paragraph". A vendor who
    clears the pacing box has almost certainly not decided the assistant should
    stop being told to go one section at a time, and the failure that causes —
    every question asked at once — surfaces days later in a customer's chat,
    where it looks like our bug.
    """
    return ((overrides or {}).get(key) or "").strip() or defaults[key]

FP_DEFAULT_OPEN = "That's everything — your package is complete and validated."

FP_DEFAULT_CLOSE = (
    "Someone picks this up with everything organized to their own rules. This was "
    "preparation only — the decisions are theirs."
)

FP_DEFAULT_DELIVER = {
    "convey": (
        "Now let's get it to {vendor}. They receive packages through Convey — their "
        "secure messaging and document-upload channel. I'll walk you through attaching "
        "everything. Your documents travel there directly, not by email, and the "
        "conversation continues in the same secure place."
    ),
    "link": (
        "Now let's deliver it. Upload the package and your documents at {vendor}'s secure "
        "link — I'll tell you exactly which files to attach. Keep sensitive documents off email."
    ),
    "email": (
        "Now let's deliver it. I'll draft the email with the recap attached. One caution I'll "
        "share honestly: email is not a secure channel — original documents like tax returns "
        "should go by a secure method; ask about their preferred option."
    ),
}

VALID_MODES = ("convey", "link", "email")


def normalize_destination(dest: Optional[dict]) -> dict:
    """Fill in a destination, tolerating partial or legacy shapes."""
    d = dict(DEFAULT_DESTINATION)
    d["outputs"] = dict(DEFAULT_DESTINATION["outputs"])
    d["texts"] = {"open": None, "close": None, "deliver": dict(DEFAULT_DESTINATION["texts"]["deliver"])}
    if not dest:
        return d
    outputs = dest.get("outputs") or {}
    for k in ("pdf", "md", "doclist"):
        if k in outputs:
            d["outputs"][k] = bool(outputs[k])
    mode = dest.get("mode")
    if mode in VALID_MODES:
        d["mode"] = mode
    d["connected"] = bool(dest.get("connected"))
    d["link"] = dest.get("link") or ""
    d["email"] = dest.get("email") or ""
    texts = dest.get("texts") or {}
    d["texts"]["open"] = texts.get("open")
    d["texts"]["close"] = texts.get("close")
    deliver = texts.get("deliver") or {}
    for m in VALID_MODES:
        d["texts"]["deliver"][m] = deliver.get(m)
    return d


# --------------------------------------------------------------------------
# Entry prompt
# --------------------------------------------------------------------------

def mcp_url(vendor_slug: str, skill_slug: str) -> str:
    return f"{MCP_BASE}?vendor={vendor_slug}&skill={skill_slug}"


def render_section(section: dict, position: int, total: int, *,
                   indent: str = "") -> str:
    """One section, written as instructions an AI can follow.

    Shared by the entry prompt and the per-section pages so a customer whose AI
    reads the pages and one whose AI does not are asked identical questions. Two
    copies of this wording drifted apart once already.
    """
    p, a = f"{indent}", f"{indent}  "
    out = [f"{p}Section {position} of {total}: {section['name']}"]
    says = (section.get("says") or "").strip()
    if says:
        out.append(f"{a}Open with: {says}")
    asks = [x.strip() for x in (section.get("asks") or []) if x and x.strip()]
    if asks:
        out.append(f"{a}Ask:")
        out.extend(f"{a}  - {x}" for x in asks)
    docs = [str(d).strip() for d in (section.get("collects") or []) if d and str(d).strip()]
    if docs:
        out.append(f"{a}Collect: {', '.join(docs)}")
    return "\n".join(out)


def render_workflow(sections: list[dict]) -> str:
    """Every section at once.

    Used by the whole-workflow page and by the copy-everything prompt — the
    fallback for an assistant that cannot open a link. Not what a start link
    carries: see `compile_entry_prompt` for why.
    """
    total = len(sections)
    return "\n\n".join(render_section(s, i, total)
                       for i, s in enumerate(sections, 1))


def _preamble(vendor_name: str, skill_name: str, total: int) -> str:
    count = f"{total} section{'s' if total != 1 else ''}" if total else "their intake"
    return (
        f"I want to work with {vendor_name} on {skill_name}.\n\n"
        f"Take me through their intake one section at a time — {count} in all. Ask "
        f"only the current section's questions, using their wording rather than your "
        f"own, wait for my answers, confirm what you captured, then move on. Keep a "
        f"running record so I can see my progress."
    )


def _closing(vendor_name: str) -> str:
    return (
        f"{GUARDRAIL}\n"
        f"When my package is complete, deliver it through {vendor_name}'s secure "
        f"channel. Not email."
    )


def _connector_clause(vendor_slug: str, skill_slug: str) -> str:
    """Prefer the connector, and make its absence a non-event.

    Currently called by nothing. The connector is out of scope for the pilot, so
    no prompt a customer can reach mentions it; this is kept intact rather than
    deleted because the reasoning below is the expensive part and it will be
    wanted again. Re-adding it is one line in each prompt builder.

    Naming tools in prompt text used to be forbidden here, for a good reason: an
    assistant told to reach an MCP server it has not got apologised and invented
    a schema rather than carrying on. A connector cannot be attached from a URL
    in a prompt, and no amount of wording changes that.

    What changed is that the server now speaks the transport, so for a customer
    who *has* added the connector this is the live workflow — sections held and
    validated server-side rather than pasted into their opening message. The two
    sentences below buy that upgrade without costing anyone else anything: the
    fallback stays in the same prompt, and the assistant is told plainly not to
    stall on the connector being missing, which is the exact failure that made
    this forbidden in the first place.
    """
    return (
        f"First, check whether the Lenni connector is available in this chat. If it "
        f"is, use it and disregard the written sections below: call lenni_start_session "
        f'with vendor "{vendor_slug}" and skill "{skill_slug}", ask exactly what it '
        f"returns, and call lenni_complete_section to receive each next section. That "
        f"path saves my progress and checks the package as we go.\n"
        f"If the connector is not there, do not try to add it and do not stop to "
        f"apologise — just work from the sections written out below."
    )


def _connector_only_prompt(vendor_name: str, vendor_slug: str, skill_name: str,
                           skill_slug: str) -> str:
    """The whole interview comes from the tools. Nothing about it is in the link.

    This is the shape the product is actually for: the vendor's questions live on
    the vendor's server, the customer's AI is a narrator, and editing a section
    in the console changes the next customer's conversation mid-flight rather
    than at the next click.

    It is opt-in per skill because it has a hard prerequisite — no connector, no
    interview. So it ends by telling the assistant to stop and say so. That
    matters more than it looks: the failure in #27 was an assistant that could
    not reach our tools and helpfully improvised a generic intake instead, and a
    customer cannot tell a guessed schema from the bank's own. Refusing loudly is
    the better failure.
    """
    return "\n\n".join([
        f"I want to work with {vendor_name} on {skill_name}.",
        (
            f"Run it with the Lenni connector: call lenni_start_session with vendor "
            f'"{vendor_slug}" and skill "{skill_slug}". Ask exactly the questions it '
            f"returns, in its wording and its order, and wait for my answers. When a "
            f"section is answered, call lenni_complete_section to get the next one. "
            f"Keep going until it reports done, then call lenni_finish."
        ),
        (
            "Do not write the questions yourself, do not reorder or skip them, and do "
            "not work from how intakes like this usually go. Everything comes from the "
            "tools. lenni_validate_package will tell you what is still missing."
        ),
        (
            "If you do not have the Lenni connector in this chat, stop and tell me "
            "plainly. Do not improvise a substitute — I need to add the connector "
            "before we can start."
        ),
        _closing(vendor_name),
    ])


ENTRY_MODES = ("inline", "doc", "staged", "connector")

DEFAULT_ENTRY_MODE = "inline"
"""What a product starts as, written down rather than left to the schema.

`CREATE TABLE IF NOT EXISTS` does not revisit a database that already exists, so
changing the column default in the schema moved nothing on any instance holding
real vendors — every new product carried on being created in the mode the column
was given years ago. Nothing failed loudly: the vendor got a working link whose
opening message asked their customer's assistant to fetch the rest, which is the
one thing we know it may not do. Setting it on the insert is the version that
cannot drift, because it does not depend on what the table remembers.
"""


def _inline_prompt(vendor_name: str, vendor_slug: str, skill_name: str,
                   skill_slug: str, sections: list[dict], final_text: str = "",
                   texts: Optional[dict] = None) -> str:
    """The whole intake travels in the opening message. Nothing is fetched.

    Every other mode asks the customer's assistant to go and get something — a
    page, a section, a tool — and that request is the part we do not control. A
    week of testing showed why it matters: one terminal fetched our live page
    every time, another quietly read a three-day-old crawl of it and recited a
    line we had deleted hours earlier, and a third has not accepted a pre-filled
    prompt since January. None of that is anybody misbehaving; it is ordinary
    variation in how vendors retrieve the web, and it changes on their schedule.

    Carrying the questions removes the dependency rather than managing it. There
    is no page to crawl, no cache to go stale, no search index to be missing from
    and no robots.txt to misread, so every terminal with a text box behaves the
    same way. The prompt is compiled at the moment of the click, which makes it
    exactly as current as a fetch would have been.

    What it costs is real and worth stating. The customer's opening message is
    long, the assistant can see the last question before it asks the first, and
    the vendor's whole question set is readable by anyone who inspects the link.
    The pacing rules below are the answer to the second; the first and third are
    the price of working everywhere, and the connector is what a vendor buys when
    the third one matters to them.

    `final_text` is the vendor's closing message, which has to be carried for the
    same reason the questions are: nothing is fetched, so anything absent from
    this message will never be said. It is quoted at the end under a condition —
    after the PDF, not before — because the summary the customer approves has to
    end on the PDF offer, and delivery details mixed into that summary would put
    something after it.

    `texts` is the vendor's wording for any of the paragraphs in
    `ENTRY_TEXT_KEYS`. What stays ours regardless is the intake, which is edited
    as sections, and the guardrail. Everything a vendor can change here is
    instruction rather than obligation, which is the line worth holding: they can
    rephrase how the assistant is asked to pace itself, and they cannot remove
    the sentence that says this is not a credit decision.
    """
    # Not `_preamble`: it explains the pacing, and so does the block below it,
    # so inline was spending 265 characters saying the same thing twice. That
    # is not free here — this prompt has to survive percent-encoding into a URL,
    # and the largest intakes now sit close enough to the ceiling that our own
    # repetition is what would push them over it and silently shorten the link.
    total = len(sections)
    parts = [f"I want to work with {vendor_name} on {skill_name}."]
    if sections:
        parts.append(
            f"--- {vendor_name}: {skill_name} — the complete intake ---\n"
            f"{render_workflow(sections)}\n"
            f"--- end of {vendor_name}'s intake ---"
        )
    defaults = entry_prompt_defaults(total)
    for key in ENTRY_TEXT_KEYS:
        parts.append(_block(texts, defaults, key))
    if final_text:
        # The vendor's closing message has to travel here like everything else.
        # Nothing is fetched in this mode, so anything not written into the
        # opening message does not exist, and the conversation was ending on a
        # downloaded file with no word about where to send it — the one moment
        # the delivery instructions are worth anything.
        #
        # Held until after the PDF on purpose. The read-back before it has to
        # end on the PDF offer and nothing else, so the delivery details cannot
        # appear alongside the summary; they are what comes next, not part of
        # the review.
        #
        # The guardrail goes above rather than below, because everything after
        # "word for word" is quoted material and an assistant has no way to tell
        # where the quote stops. Below, it read as the last line of the vendor's
        # closing message.
        parts.append(GUARDRAIL)
        parts.append(
            "After the PDF exists — and only then — close with this, word for "
            f"word:\n\n{final_text}"
        )
    else:
        parts.append(_closing(vendor_name))
    return "\n\n".join(parts)


def _doc_handoff_prompt(vendor_name: str, skill_name: str, total: int, *,
                        guide_url: str, card_url: str) -> str:
    """Point at the vendor's guide and get out of the way.

    The shape the product is sold on: a customer on a bank's product page clicks
    through to their own assistant, which reads the bank's published guide and
    runs the conversation from it. Nothing about the intake travels in the link,
    and — unlike connector-only — nothing has to be installed first. That is what
    makes it demonstrable on a stranger's laptop.

    Its one real weakness is that fetching is a request, not a guarantee: an
    assistant with browsing off, or behind a network that blocks us, will
    cheerfully produce a generic loan intake instead and sound certain. The
    prompt therefore asks it to say so rather than improvise. Whether the fetch
    happened is answered by the server, which logs the read — asking the
    assistant to announce it only moved the problem into the customer's chat.
    """
    return "\n\n".join([
        f"I want to work with {vendor_name} on {skill_name}.",
        (
            f"Their workflow for assistants is at {guide_url} — please read it before "
            f"asking me anything. It carries every section and every question they "
            f"want asked, so you will not need to fetch anything else. The short card "
            f"is at {card_url}."
        ),
        (
            f"Then take me through their {total} sections one at a time, in the order "
            f"and the wording on that page. Wait for my answers to each section, read "
            f"back what you captured, and only then move to the next one. Do not show "
            f"me later sections early or ask for everything at once."
        ),
        (
            "If you cannot open the links, say so plainly and I will paste the "
            "sections in myself — please do not fill the gap with how intakes like "
            "this usually go, because theirs is not that. If you can, just start with "
            "their first section; no need to tell me what you read."
        ),
        _closing(vendor_name),
    ])


def compile_entry_prompt(*, vendor_name: str, vendor_slug: str, skill_name: str,
                         skill_slug: str, sections: list[dict],
                         next_url: str = "", workflow_url: str = "",
                         mode: str = "staged", guide_url: str = "",
                         card_url: str = "", dest: Optional[dict] = None,
                         texts: Optional[dict] = None) -> str:
    """The text a start link carries into the customer's AI.

    Inlining every section was rejected once, for reasons that were all true: a
    long opening message, an AI that can see the ending before it begins and so
    races ahead, and the vendor's whole question set readable by anyone with the
    link. It is now the default anyway, because live testing showed the thing it
    trades those costs for — never having to ask a terminal to fetch anything —
    is worth more than all three. See `_inline_prompt`.

    None of the prompts a customer can currently reach mention the connector. It
    is built and still selectable, but it is out of scope for the pilot, and a
    prompt that names a tool the customer has not got is how #27 happened: the
    assistant stalled, apologised, then invented a schema. Nothing in the
    connector itself was removed — see `_connector_clause`, which is kept for
    when it comes back.

    Whichever shape is chosen, it is instruction-following rather than
    enforcement: nothing here validates an answer or saves progress if the
    assistant decides to do otherwise. That gap is exactly what the connector
    closes, which is the argument for eventually turning it back on.

    Four shapes, chosen per skill by `mode`:

    * `inline` — the whole intake travels in the prompt and nothing is fetched.
      The default, because it is the only one that does not depend on how a
      given terminal decides to retrieve the web. See `_inline_prompt`. Pass
      `dest` for this one: it is where the closing message comes from, and
      without it the conversation ends on a downloaded PDF and silence. `texts`
      carries any paragraphs the vendor has rewritten.
    * `doc` — points at the vendor's published guide and section 1, carries no
      questions, and needs nothing installed. See `_doc_handoff_prompt`.
    * `staged` — this function's body: section 1 inlined, the rest chained.
    * `connector` — the interview lives entirely in the tools, and the prompt
      refuses without them. See `_connector_only_prompt`.

    Regenerate on every save — the wording is embedded, so an unrecompiled edit
    ships the old question to every customer who clicks after it.
    """
    if mode == "connector":
        return _connector_only_prompt(vendor_name, vendor_slug, skill_name, skill_slug)

    if mode == "inline":
        # Compiled here rather than passed as text so the customer always gets
        # the vendor's current closing message — the same reason the sections
        # themselves are recompiled on every save.
        final_text = (
            compile_final_prompt(dest=dest or {}, vendor_name=vendor_name)
            .text(for_customer=True)
            if dest is not None else ""
        )
        return _inline_prompt(vendor_name, vendor_slug, skill_name, skill_slug,
                              sections, final_text, texts)

    if mode == "doc" and guide_url:
        return _doc_handoff_prompt(vendor_name, skill_name, len(sections),
                                   guide_url=guide_url, card_url=card_url)

    total = len(sections)
    parts = [_preamble(vendor_name, skill_name, total)]

    if sections:
        parts.append(f"--- {render_section(sections[0], 1, total)}\n--- end of section 1")

    if total > 1:
        if next_url:
            parts.append(
                f"When section 1 is answered and confirmed, open {next_url} for "
                f"section 2, and keep going that way — each section names the one "
                f"after it. Do not read ahead or list later sections before we get "
                f"to them. If you cannot open links, tell me and I will paste each "
                f"section in myself."
            )
        elif workflow_url:
            parts.append(
                f"The remaining sections are at {workflow_url} . Ask them in order, "
                f"one at a time, and do not show me later ones in advance."
            )

    parts.append(_closing(vendor_name))
    return "\n\n".join(parts)


def compile_full_entry_prompt(*, vendor_name: str, skill_name: str,
                              sections: list[dict], vendor_slug: str = "",
                              skill_slug: str = "") -> str:
    """Every section inlined, for an assistant that cannot open a link.

    The floor under the staged prompt. Some assistants have browsing switched off,
    and some customers are behind a network that blocks us; a copy-and-paste of
    this always works. Offered on the start page rather than sent by default,
    because everything in the `compile_entry_prompt` docstring is still true of it.

    Says nothing about the connector, like every prompt a customer can currently
    reach. The connector is built and still selectable per skill, but it is not
    part of the pilot, and naming a thing the customer cannot act on only invites
    the assistant to stall on its absence — the #27 failure.
    """
    parts = [_preamble(vendor_name, skill_name, len(sections))]
    if sections:
        parts.append(
            f"--- {vendor_name}: {skill_name} ---\n"
            f"{render_workflow(sections)}\n"
            f"--- end of {vendor_name}'s intake ---\n"
            f"Ask these in order. Do not show me a section before we reach it."
        )
    parts.append(_closing(vendor_name))
    return "\n\n".join(parts)


def compile_short_entry_prompt(*, vendor_name: str, skill_name: str,
                               section_names: list[str], workflow_url: str,
                               vendor_slug: str = "", skill_slug: str = "") -> str:
    """The compact form, for when even the first section will not fit in a URL.

    Names the sections so the customer can see the shape, and points at the
    published copy for the wording. Weaker than the staged prompt because it needs
    the AI to fetch something before it can ask anything at all — which is why it
    is the fallback and not the default.
    """
    parts = [_preamble(vendor_name, skill_name, len(section_names))]
    if section_names:
        listing = "  ".join(f"{i}) {n}" for i, n in enumerate(section_names, 1))
        parts.append(f"The sections are: {listing}.")
    parts.append(
        f"Open {workflow_url} and follow the questions there exactly as written, "
        f"one section at a time."
    )
    parts.append(_closing(vendor_name))
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# Final prompt
# --------------------------------------------------------------------------

@dataclass
class FinalPrompt:
    """The closing message, split into what a vendor may edit and what they may not."""
    opening: str
    produced: str
    delivery: str
    link_line: str
    sign_off: str
    locked: tuple[str, ...] = field(default=("produced", "link_line"))
    # Whether `link_line` names a real destination or is a note to the vendor
    # about configuring one.
    link_resolved: bool = True

    def text(self, *, for_customer: bool = False) -> str:
        """The closing message.

        `for_customer` drops an unresolved link line. Unconfigured, that line
        reads "(connect Convey in the destination settings to finalize the
        delivery link)" — which is addressed to the vendor, in the vendor's
        console, and is the correct thing to show them. It became reachable by
        customers when the closing message started travelling inside the entry
        prompt, where it would arrive as an instruction to the borrower to go
        and configure our software.

        Publishing is blocked without a working destination, so this only bites
        on drafts. Drafts are what everyone demos.
        """
        link = self.link_line if (self.link_resolved or not for_customer) else ""
        delivery = f"{self.delivery}\n{link}" if link else self.delivery
        return "\n\n".join(
            part for part in (self.opening, self.produced, delivery, self.sign_off)
            if part
        )


def _produced_sentence(dest: dict) -> str:
    outs = []
    if dest["outputs"]["pdf"]:
        outs.append("your PDF package — the full recap of everything we completed, formatted for review")
    if dest["outputs"]["md"]:
        outs.append("the markdown files of every section, so any system or AI can pick this up")
    if dest["outputs"]["doclist"]:
        outs.append("your required-documents list — what's attached, what's still owed and by when")
    if not outs:
        return "I'll give you the full recap right here — no files were configured."
    if len(outs) == 1:
        body = outs[0]
    else:
        body = ", ".join(outs[:-1]) + ", and " + outs[-1]
    return f"I've produced {body}."


def _link_line(dest: dict, vendor_name: str) -> str:
    if dest["mode"] == "convey":
        if dest["connected"] and dest["link"]:
            return f"-> Secure upload: {dest['link']}"
        return "-> (connect Convey in the destination settings to finalize the delivery link)"
    if dest["mode"] == "link":
        return f"-> Upload here: {dest['link'] or '(add your secure link in the destination settings)'}"
    return f"-> Send to: {dest['email'] or '(add the email in the destination settings)'}"


def compile_final_prompt(*, dest: dict, vendor_name: str) -> FinalPrompt:
    d = normalize_destination(dest)
    texts = d["texts"]
    deliver_default = FP_DEFAULT_DELIVER[d["mode"]].format(vendor=vendor_name)
    return FinalPrompt(
        opening=texts["open"] or FP_DEFAULT_OPEN,
        produced=_produced_sentence(d),
        delivery=texts["deliver"][d["mode"]] or deliver_default,
        link_line=_link_line(d, vendor_name),
        sign_off=texts["close"] or FP_DEFAULT_CLOSE,
        link_resolved=bool(d["link"] if d["mode"] in ("convey", "link") else d["email"]),
    )


def destination_label(dest: dict, vendor_name: str) -> str:
    """One-line description of the exit, for the section flow footer."""
    d = normalize_destination(dest)
    if d["mode"] == "convey":
        if d["connected"]:
            return f"delivered to {vendor_name}'s Convey — secure messaging and document upload"
        return "delivered to Convey (not connected yet)"
    if d["mode"] == "link":
        target = d["link"].replace("https://", "").replace("http://", "")
        return f"sent to their secure link ({target[:40]})" if target else "sent to their secure link (not set yet)"
    return f"emailed to {d['email']}" if d["email"] else "emailed (address not set yet)"


def delivery_warnings(dest: dict) -> list[str]:
    """Honesty checks surfaced in the console and blocked at publish where fatal."""
    d = normalize_destination(dest)
    out: list[str] = []
    if d["mode"] == "email":
        out.append(
            "Email is the least secure option for sensitive documents — fine for summaries; "
            "consider Convey or a secure link for the full package."
        )
        if not d["email"]:
            out.append("No delivery email set — the skill cannot be published without one.")
    if d["mode"] == "link" and not d["link"]:
        out.append("No secure link set — the skill cannot be published without one.")
    if d["mode"] == "convey" and not d["connected"]:
        out.append("Convey is not connected — the skill cannot be published without a destination.")
    if not any(d["outputs"].values()):
        out.append("No outputs are enabled, so the customer finishes with nothing to hand over.")
    return out


def can_publish(dest: dict) -> tuple[bool, list[str]]:
    """A skill with no working destination is a dead end. Refuse to publish it."""
    d = normalize_destination(dest)
    blockers: list[str] = []
    if d["mode"] == "convey" and not d["connected"]:
        blockers.append("Connect Convey, or choose a secure link or email destination.")
    if d["mode"] == "link" and not d["link"]:
        blockers.append("Add the secure link the package should be delivered to.")
    if d["mode"] == "email" and not d["email"]:
        blockers.append("Add the email address the package should be delivered to.")
    if not any(d["outputs"].values()):
        blockers.append("Enable at least one output — PDF, markdown, or the document list.")
    return (not blockers), blockers
