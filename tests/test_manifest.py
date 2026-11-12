"""The agent manifest — regenerates on publish, drafts omitted."""
from __future__ import annotations

import re

from a2b import manifest, skills
from a2b.compiler import (PDF_OFFER, SECTION_CHECK, SUMMARY_OFFER,
                          WORK_FROM_THE_PAGE, destination_label)
from a2b.config import GUARDRAIL
from a2b.links import hosted_start_url, skill_doc_url


def test_drafts_are_omitted_from_both_manifest_forms(vendor_id):
    draft = skills.seed_skill_from_template(vendor_id, "uw-cre-owner-occupied")
    live = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.connect_convey(live, "https://bluebonnet.convey.lenni.io")
    skills.publish_skill(live)

    txt = manifest.llms_txt(vendor_id)
    md = manifest.llms_md(vendor_id)

    assert "Multifamily Permanent" in txt and "Multifamily Permanent" in md
    assert "Owner-Occupied CRE" not in txt
    assert "Owner-Occupied CRE" not in md
    assert skills.get_skill(draft)["status"] == "draft"


def test_publishing_adds_the_service_to_the_manifest(vendor_id):
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    assert "Multifamily Permanent" not in manifest.llms_md(vendor_id)

    skills.connect_convey(sid, "https://bluebonnet.convey.lenni.io")
    skills.publish_skill(sid)
    assert "Multifamily Permanent" in manifest.llms_md(vendor_id)

    skills.unpublish_skill(sid)
    assert "Multifamily Permanent" not in manifest.llms_md(vendor_id)


def _publish(vendor_id, template="uw-multifamily-perm"):
    sid = skills.seed_skill_from_template(vendor_id, template)
    skills.connect_convey(sid, "https://bluebonnet.convey.lenni.io")
    skills.publish_skill(sid)
    return skills.get_skill(sid)


def test_a_product_gets_its_own_pair_of_agent_docs(vendor_id):
    """The vendor manifest answers what this bank offers. An assistant that was
    handed a loan product needs what this loan is and where it starts, and given
    only the vendor page it picks one of six services and sounds certain."""
    s = _publish(vendor_id)
    card = manifest.skill_llms_txt(vendor_id, s["slug"])
    guide = manifest.skill_context_md(vendor_id, s["slug"])

    assert s["name"] in card and s["name"] in guide
    # The card's job is to send an assistant to the workflow, not to be one.
    assert "context.md" in card


def test_the_guide_carries_the_entire_workflow(vendor_id):
    """One fetch has to be enough to run the whole interview. The version that made
    the assistant fetch each following section put a network call between every
    section and the next, and the first failure stopped an interview with the
    customer halfway through a sentence."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])

    for i, section in enumerate(s["sections"], 1):
        assert section["name"] in guide
        assert (section["says"] or "").strip() in guide, f"section {i} lost its opener"
        for question in section["asks"]:
            assert question in guide, f"section {i} is missing: {question!r}"


def test_the_guide_matches_the_section_pages_word_for_word(vendor_id):
    """Two renderings of the same questions drifted apart once already. An assistant
    working from the guide and one working from the pages must be asked the same
    thing, or a bank's intake depends on which route their customer's AI took."""
    from a2b.compiler import render_section

    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])
    total = len(s["sections"])

    for i, section in enumerate(s["sections"], 1):
        body = "\n".join(render_section(section, i, total).splitlines()[1:])
        assert body in guide, f"section {i} is worded differently in the guide"


def test_the_guide_spells_out_the_pacing(vendor_id):
    """Carrying every question in one document is what makes an assistant able to
    finish. It is also what tempts it to ask everything at once, so the rules that
    stop that have to be explicit rather than implied."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"]).lower()

    assert "one section at a time" in guide
    assert "do not show later sections in advance" in guide
    assert "read back what you captured" in guide
    for i in range(1, len(s["sections"])):
        assert f"ask section {i + 1} from" in guide, f"no handover after section {i}"


def test_the_interview_ends_by_confirming_before_it_produces_anything(vendor_id):
    """What the customer is about to hand a bank is their own account of their own
    business. A mistake they never saw is worse than a gap they did, so the package
    gets read back and corrected before it becomes a document."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"]).lower()

    assert "tell me which section and what to change" in guide
    assert "change only that section" in guide
    assert "do not produce anything until they have checked it" in guide
    # Confirmation means make it — a second round of asking is its own failure.
    assert "do not ask a second time" in guide


def test_the_questions_are_preceded_by_an_order_to_use_them(vendor_id):
    """An assistant that has seen a thousand loan intakes will supply the question it
    expects instead of the one the vendor wrote, and sound right doing it. The
    instruction sits against the first question rather than up in the preamble."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])

    assert WORK_FROM_THE_PAGE in guide
    first_question = guide.index("### Section 1")
    assert guide.index(WORK_FROM_THE_PAGE) < first_question
    assert guide.index("## The workflow") < guide.index(WORK_FROM_THE_PAGE)


def test_each_section_hands_over_by_asking_the_customer_to_check_it(vendor_id):
    """An assistant that reads a capture back and keeps talking has decided for the
    customer that it heard them right. The advance is theirs to give — which is also
    why the final package is one they have already approved section by section."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])

    # Every handover, not just the first: n sections means n-1 of them.
    assert guide.count(SECTION_CHECK) == len(s["sections"]) - 1
    assert "not because the answers looked complete to you" in guide


def test_the_last_section_says_what_to_do_next_like_every_other_one(vendor_id):
    """A live run ended on "that completes all 4 sections" and stopped. Every other
    section names the next move; the last one used to just stop, leaving the closing
    rules a long way back in a long conversation — so they were never reached."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])
    total = len(s["sections"])

    tail = guide[guide.index(f"### Section {total} of {total}"):]
    assert "the interview is over but you are not finished" in tail
    closing = tail[:tail.index("## Finishing")]
    # Both beats, in order: offer the summary, then offer the PDF.
    assert closing.index(SUMMARY_OFFER) < closing.index(PDF_OFFER)


def test_the_guide_asks_to_be_re_read_before_every_section(vendor_id):
    """A single fetch decays. By section three the assistant is working from its own
    paraphrase and will ask a paraphrased question in the bank's name — which is what
    a live run did, renaming two sections. Re-reading also picks up a mid-session
    edit, and leaves a read per section in the log so drift is visible."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])
    url = skill_doc_url(skills.get_vendor(vendor_id)["slug"], s["slug"], "context.md")

    assert "Re-read this page before each section" in guide
    # Named at each handover, not just once in the preamble where it will be missed.
    assert guide.count(f"re-read {url}") == len(s["sections"]) - 1


def test_a_failed_re_read_does_not_stop_the_interview(vendor_id):
    """The chained-fetch design died because one failed request stranded a customer
    mid-sentence. Re-reading is only safe to ask for while the whole workflow is
    already in context, and the page has to say that a failure changes nothing."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])

    assert "If the re-fetch fails, carry on from what you already have" in guide
    assert "You are not stuck" in guide


def test_the_summary_is_offered_before_it_is_delivered(vendor_id):
    """Four sections dumped back unannounced is a wall of text nobody reads to the
    bottom of. A customer who asked for the summary reads it; one handed a summary
    skims it, and skimming is how a wrong figure reaches a lender."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])

    assert SUMMARY_OFFER in guide
    finishing = guide[guide.index("## Finishing"):]
    assert finishing.index(SUMMARY_OFFER) < finishing.index("show the complete package")
    assert "wait" in finishing[:finishing.index("show the complete package")]


def test_the_read_back_always_ends_by_offering_the_pdf(vendor_id):
    """A package that ends on a list of missing documents reads as homework, and the
    customer never learns a document was one sentence away."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])

    assert PDF_OFFER in guide
    assert "word for word, every single time" in guide
    assert "Nothing goes after it" in guide
    # Outstanding items get asked for before the offer, not instead of it.
    outstanding = [ln for ln in guide.splitlines()
                   if "tell me to go ahead" in ln]
    assert outstanding and outstanding[0].rstrip().endswith(PDF_OFFER)


def test_delivery_is_held_back_until_the_file_exists(vendor_id):
    """The live run ended on 'I have not sent anything to the bank yet', which is true
    and useless — it displaced the one line the customer had to act on."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])

    assert "Delivery is not part of the read-back" in guide
    assert guide.index(PDF_OFFER) < guide.index("After the PDF exists")


def test_nothing_asks_the_assistant_to_report_on_its_own_reading(vendor_id):
    """Whether the fetch happened is the server's answer, not the assistant's. Asking
    for it in the chat put our plumbing in front of the customer — codes in the first
    reply, codes signing the finished package — and took the word of the one party
    that might be making it up."""
    s = _publish(vendor_id)
    surfaces = [manifest.skill_context_md(vendor_id, s["slug"]),
                manifest.skill_llms_txt(vendor_id, s["slug"]),
                manifest.llms_md(vendor_id),
                manifest.llms_txt(vendor_id),
                skills.get_skill(s["id"])["entry_prompt"]]

    for text in surfaces:
        assert not re.search(r"A2B-[0-9A-F]{8}", text)
        assert "quote the confirmation code" not in text.lower()
    # And it still has to admit a failed fetch rather than improvising over it.
    assert "invented one is worse than none" in surfaces[0]


def test_the_pdf_has_to_carry_the_gaps_and_the_guardrail(vendor_id):
    """A package that lists only what was answered reads as complete. The bank needs
    the holes, and the document has to say on its face that it is not a decision."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])

    assert "Still outstanding" in guide
    assert GUARDRAIL in guide


def test_the_assistant_is_told_it_cannot_deliver_or_fake_a_file(vendor_id):
    """Two lies a helpful assistant tells at the end: that it filed the package, and
    that it made a file it cannot make. Either one sends a customer away believing
    their application is with the bank when nothing was sent."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])
    lowered = guide.lower()

    assert "you have no channel to" in lowered
    assert "do not say you have sent" in lowered
    assert "if you cannot create files" in lowered
    assert "do not describe a file you have not" in lowered
    # And it still has to say where the customer takes it themselves.
    assert destination_label(s["destination"],
                             skills.get_vendor(vendor_id)["name"]) in guide


def test_an_edit_in_the_console_reaches_the_next_fetch(vendor_id):
    """The pitch is that a bank changes their questions, not their website. These
    documents are generated per request, and this is the test that keeps it that
    way — a cached or precompiled guide would serve last week's intake."""
    s = _publish(vendor_id)
    assert "What is the borrower's exit?" not in manifest.skill_context_md(
        vendor_id, s["slug"])

    first = s["sections"][0]
    skills.update_section(first["id"], asks=["What is the borrower's exit?"])

    guide = manifest.skill_context_md(vendor_id, s["slug"])
    assert "What is the borrower's exit?" in guide


def test_the_guide_refuses_rates_and_never_demands_the_connector(vendor_id):
    """Two failures this doc exists to prevent: an assistant that quotes a rate on
    a bank's behalf, and one that stops dead because it has no connector. The
    second is what made the connector-only prompt unusable for this flow."""
    s = _publish(vendor_id)
    guide = manifest.skill_context_md(vendor_id, s["slug"])

    assert "rate" in guide.lower() and "approval" in guide.lower()
    assert "do not stop" in guide.lower()
    assert "nothing here needs it" in guide.lower()


def test_product_docs_refuse_to_describe_a_draft(vendor_id):
    """Publishing is the consent moment. A draft is as invisible here as it is in
    the vendor manifest, or a vendor's unfinished work leaks through a guessed URL."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-cre-owner-occupied")
    slug = skills.get_skill(sid)["slug"]

    for fn in (manifest.skill_llms_txt, manifest.skill_context_md):
        try:
            fn(vendor_id, slug)
            assert False, f"{fn.__name__} described a draft"
        except KeyError:
            pass


def test_manifest_carries_the_guardrail_and_the_connector_address(vendor_id):
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.connect_convey(sid, "https://bluebonnet.convey.lenni.io")
    skills.publish_skill(sid)

    md = manifest.llms_md(vendor_id)
    assert "not a credit decision" in md
    assert "vendor=bluebonnet-community-bank" in md
    assert "Collateral -> Deal -> Borrower" in md


def test_manifest_does_not_imply_an_agent_can_attach_itself(vendor_id):
    """The address is published so a user can register it. An assistant reading
    this page cannot connect itself to it, and saying otherwise is what sent
    assistants off to fetch the endpoint and improvise a schema."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.connect_convey(sid, "https://bluebonnet.convey.lenni.io")
    skills.publish_skill(sid)

    md = manifest.llms_md(vendor_id).lower()
    assert "cannot be attached from this page" in md
    assert "lenni_start_session" in md


def test_manifest_states_where_a_finished_package_goes(vendor_id):
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.connect_convey(sid, "https://bluebonnet.convey.lenni.io")
    skills.publish_skill(sid)
    assert "Convey" in manifest.llms_md(vendor_id)


def test_agents_page_shows_drafts_to_the_vendor_but_marks_them_unrunnable(vendor_id):
    """The vendor looking at their own agent page should see what an agent is
    NOT being shown — that is what makes publishing legible as a consent step."""
    skills.seed_skill_from_template(vendor_id, "uw-cre-owner-occupied")
    live = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.connect_convey(live, "https://bluebonnet.convey.lenni.io")
    skills.publish_skill(live)

    data = manifest.agents_page_data(vendor_id)
    by_name = {s["name"]: s for s in data["services"]}
    assert by_name["Owner-Occupied CRE"]["runnable"] is False
    assert by_name["Multifamily Permanent"]["runnable"] is True
    assert "Owner-Occupied CRE" not in data["llms_md"]


def test_webmaster_packet_is_two_lines_per_product(vendor_id):
    s = _publish(vendor_id)
    packet = manifest.webmaster_packet(vendor_id)

    assert 'rel="llms"' in packet["head"]
    assert "bluebonnetbank.com/llms.txt" in packet["head"]

    service = next(x for x in packet["services"] if x["slug"] == s["slug"])
    assert f"/{s['slug']}/llms.txt" in service["head"], "product page gets the product card"
    assert f"/start/{s['slug']}" in service["button"]


def test_the_embed_button_works_the_day_it_is_pasted(vendor_id):
    """It points at the address this deployment serves, not the vendor's own domain.
    A button that 404s until the bank wires up a path is a button that gets pasted,
    tested once, and quietly removed."""
    s = _publish(vendor_id)
    button = next(x for x in manifest.webmaster_packet(vendor_id)["services"]
                  if x["slug"] == s["slug"])["button"]

    assert hosted_start_url(skills.get_vendor(vendor_id)["slug"], s["slug"]) in button
    assert "bluebonnetbank.com" not in button
    # No build step, no framework, and the guardrail travels with it.
    assert "<script" not in button and "class=" not in button
    assert "makes all decisions" in button


def test_a_draft_gets_no_button(vendor_id):
    skills.seed_skill_from_template(vendor_id, "uw-cre-owner-occupied")
    slugs = {s["slug"] for s in manifest.webmaster_packet(vendor_id)["services"]}
    assert "cre-owner-occupied" not in slugs


def test_start_link_prefers_the_vendors_own_domain(vendor_id):
    from a2b.links import universal_link

    assert universal_link("bluebonnetbank.com", "bb", "multifamily-perm") == \
        "https://bluebonnetbank.com/start/multifamily-perm"
    # No domain on file: fall back to a Lenni-hosted path rather than a broken link.
    assert universal_link("", "bb", "multifamily-perm") == \
        "https://lenni.io/bb/start/multifamily-perm"


def test_supported_and_url_never_disagree():
    """`supported` is what the console trusts when it decides whether to render a
    row as an openable link. The two must never drift apart: a supported terminal
    with no URL renders a dead anchor, and an unsupported one carrying a URL is
    exactly the "aspirational link shipped as real" case this flag exists to stop.

    Which terminals are supported, and how each behaves on arrival, is covered in
    test_links.py — asserting the roster here is what made this test go stale.
    """
    from a2b.links import terminal_links

    for t in terminal_links("some prompt"):
        assert t["supported"] == bool(t["url"]), t
        if t["desktop_url"]:
            assert t["supported"], t
