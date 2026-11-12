"""Prompt compilation — config in, prompt out, no hand-writing."""
from __future__ import annotations

from a2b import skills
from a2b.compiler import (FRESH_CHAT_NOTE, PDF_TRIGGER, REVIEW_THEN_PDF, SECTION_CHECK,
                          can_publish, compile_final_prompt, normalize_destination)
from a2b.config import GUARDRAIL


def test_entry_prompt_names_the_vendor_and_the_skill(vendor_id):
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert "Bluebonnet Community Bank" in prompt
    assert "Multifamily Permanent" in prompt
    assert "Section 1 of 3: Collateral" in prompt


def test_entry_prompt_carries_the_first_sections_questions_verbatim(vendor_id):
    """The failure this exists to prevent: the prompt named the sections and told
    the AI to fetch the rest over WebMCP, which no chat client can do from a URL
    in a message. The AI improvised its own questions and the customer walked
    through an intake that was not the vendor's."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    first = skills.get_skill(sid)["sections"][0]
    prompt = skills.get_skill(sid)["entry_prompt"]

    for question in first["asks"]:
        assert question in prompt, f"section 1 lost: {question!r}"


def test_staged_mode_withholds_every_later_section(vendor_id):
    """Staged mode's entire reason to exist: the customer's opening message is one
    section long, so the AI cannot see the ending and race ahead, and a start link
    does not publish the vendor's whole question set.

    No longer the default — inline pays all of those costs deliberately in
    exchange for never having to fetch anything — but a vendor who picks staged
    must still get this.
    """
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_entry_mode(sid, "staged")
    skill = skills.get_skill(sid)
    prompt = skill["entry_prompt"]

    for section in skill["sections"][1:]:
        for question in section["asks"]:
            assert question not in prompt, f"section {section['name']!r} leaked: {question!r}"

    # Counting headers rather than characters: a section with no questions would
    # slip past the loop above, and total length is dominated by the fixed
    # preamble, so it says little about how many sections are in there.
    assert prompt.count("Section ") == 1, "more than one section is in the prompt"


def test_staged_mode_hands_over_the_address_of_section_two(vendor_id):
    """Without this the customer's AI finishes section 1 and stops."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_entry_mode(sid, "staged")
    prompt = skills.get_skill(sid)["entry_prompt"]
    assert "/start/multifamily-perm/2.md" in prompt


def test_a_one_section_skill_does_not_promise_a_second(vendor_id):
    from a2b.compiler import compile_entry_prompt
    prompt = compile_entry_prompt(
        vendor_name="V", vendor_slug="v", skill_name="S", skill_slug="s",
        sections=[{"name": "Only", "says": "hi", "asks": ["a?"], "collects": []}],
        next_url="https://example.com/v/v/start/s/2.md",
    )
    assert "Section 1 of 1" in prompt
    assert "2.md" not in prompt


def test_the_copy_everything_prompt_still_has_all_of_it(vendor_id):
    """The floor for an assistant with browsing switched off."""
    from a2b.compiler import compile_full_entry_prompt
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skill = skills.get_skill(sid)

    full = compile_full_entry_prompt(vendor_name="Bluebonnet Community Bank",
                                     skill_name=skill["name"],
                                     sections=skill["sections"])
    for section in skill["sections"]:
        for question in section["asks"]:
            assert question in full


def test_the_connector_only_column_reaches_a_database_that_already_exists():
    """`CREATE TABLE IF NOT EXISTS` leaves an existing database untouched, so a
    new column in SCHEMA never arrives on one. The deployed instance holds real
    vendor skills and cannot be recreated, so the migration is the only way the
    toggle exists there at all."""
    import sqlite3

    from a2b.db import _migrate

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE vendor_skill (id TEXT PRIMARY KEY, entry_prompt TEXT)")

    _migrate(conn)
    assert "connector_only" in {r["name"] for r in conn.execute("PRAGMA table_info(vendor_skill)")}

    _migrate(conn)
    added = [r for r in conn.execute("PRAGMA table_info(vendor_skill)")
             if r["name"] == "connector_only"]
    assert len(added) == 1, "migration ran twice and added the column twice"


def test_a_skill_already_set_to_connector_only_keeps_that_choice():
    """The deployed instance has skills a vendor deliberately routed at the tools.
    Adding entry_mode with a default would silently move them back to a mode that
    publishes section 1 in the link — a setting change nobody asked for, on a
    vendor's live intake."""
    import sqlite3

    from a2b.db import _migrate

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE vendor_skill (id TEXT PRIMARY KEY, entry_prompt TEXT)")
    _migrate(conn)

    conn.execute("INSERT INTO vendor_skill (id, connector_only) VALUES ('a', 1)")
    conn.execute("INSERT INTO vendor_skill (id, connector_only) VALUES ('b', 0)")
    conn.execute("ALTER TABLE vendor_skill DROP COLUMN entry_mode")
    _migrate(conn)

    modes = {r["id"]: r["entry_mode"] for r in
             conn.execute("SELECT id, entry_mode FROM vendor_skill")}
    assert modes == {"a": "connector", "b": "staged"}

    # And never again: a vendor who moves off connector-only must stay off it.
    conn.execute("UPDATE vendor_skill SET entry_mode='doc' WHERE id='a'")
    _migrate(conn)
    assert conn.execute(
        "SELECT entry_mode FROM vendor_skill WHERE id='a'").fetchone()["entry_mode"] == "doc"


def test_connector_only_carries_no_workflow_at_all(vendor_id):
    """The shape the product is for: the link is an instruction to call the
    tools, and every question stays on the vendor's server until the customer
    has earned it."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_entry_mode(sid, "connector")
    skill = skills.get_skill(sid)
    prompt = skill["entry_prompt"]

    assert "lenni_start_session" in prompt
    assert "lenni_complete_section" in prompt
    assert "Section 1 of" not in prompt
    for section in skill["sections"]:
        for question in section["asks"]:
            assert question not in prompt, f"leaked into the link: {question!r}"


def test_connector_only_refuses_rather_than_improvising(vendor_id):
    """It has a hard prerequisite, so its failure has to be loud. An assistant
    that quietly substitutes a generic intake gives the customer something they
    cannot tell apart from the bank's own — the #27 failure."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_entry_mode(sid, "connector")
    prompt = skills.get_skill(sid)["entry_prompt"].lower()

    assert "stop and tell me" in prompt
    assert "do not improvise" in prompt


def test_every_mode_is_reachable_and_reversible(vendor_id):
    """Four shapes, one setting. A vendor who tries connector-only and finds their
    customers stuck has to be able to get back, and getting back means recompiling
    the prompt — not just flipping a column."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    assert skills.get_skill(sid)["entry_mode"] == "inline"
    assert "Section 3 of 3" in skills.get_skill(sid)["entry_prompt"]

    skills.set_entry_mode(sid, "connector")
    assert "Section 1 of 3" not in skills.get_skill(sid)["entry_prompt"]

    skills.set_entry_mode(sid, "doc")
    assert "Section 1 of 3" not in skills.get_skill(sid)["entry_prompt"]
    assert "context.md" in skills.get_skill(sid)["entry_prompt"]

    skills.set_entry_mode(sid, "staged")
    prompt = skills.get_skill(sid)["entry_prompt"]
    assert "Section 1 of 3" in prompt and "Section 3 of 3" not in prompt

    skills.set_entry_mode(sid, "inline")
    assert "Section 3 of 3" in skills.get_skill(sid)["entry_prompt"]

    try:
        skills.set_entry_mode(sid, "whatever")
        assert False, "accepted an unknown mode"
    except ValueError:
        pass


def test_a_new_skill_is_inline_without_anyone_choosing_it(vendor_id):
    """The default is the whole product decision. Every other mode asks the
    customer's assistant to fetch something, and a week of testing showed that
    request is answered differently by every terminal and differently by the same
    terminal on different days."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    assert skills.get_skill(sid)["entry_mode"] == "inline"


def test_a_product_is_inline_even_on_a_database_that_predates_inline(vendor_id):
    """The test above passes on a database built from today's schema, which is
    every test database and no real one. `CREATE TABLE IF NOT EXISTS` skips a
    table that already exists, so the deployed instance kept the default it was
    created with and quietly made every new product fetch-based — a working link
    whose opening message asks the customer's assistant to go and get the rest.

    Reproduced here by putting the old default back, which is the only way to see
    the difference between relying on the column and naming the value."""
    from a2b.db import cursor

    # Rebuilt from the table's own definition rather than a copy of it, so this
    # keeps reproducing the old database as the schema moves on. SQLite will not
    # let sqlite_master be edited in place, and the table is empty at this point.
    with cursor() as cur:
        current = cur.execute("SELECT sql FROM sqlite_master WHERE name='vendor_skill'"
                              ).fetchone()["sql"]
    legacy = current.replace("DEFAULT 'inline'", "DEFAULT 'staged'")
    assert legacy != current, "no inline default to age; this test has stopped testing"
    with cursor() as cur:
        cur.execute("DROP TABLE vendor_skill")
        cur.execute(legacy)

    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    assert skills.get_skill(sid)["entry_mode"] == "inline"

    sid = skills.create_custom_skill(vendor_id, "Equipment Finance")
    assert skills.get_skill(sid)["entry_mode"] == "inline"


def test_inline_carries_every_question_the_vendor_wrote(vendor_id):
    """The whole point: the customer's assistant is handed the intake rather than
    told where to find it, so there is no fetch to fail, no cache to be stale and
    no crawl to be missing from."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skill = skills.get_skill(sid)
    prompt = skill["entry_prompt"]

    for section in skill["sections"]:
        assert section["name"] in prompt
        for question in section["asks"]:
            assert question in prompt, f"lost from the link: {question!r}"


def test_inline_sends_the_assistant_nowhere(vendor_id):
    """A single fetchable address is a single point of failure, and the modes that
    carry one are exactly the ones that broke in testing. Inline must not quietly
    reintroduce the dependency it exists to remove."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert "http://" not in prompt and "https://" not in prompt
    assert "context.md" not in prompt and "llms.txt" not in prompt
    assert ".md" not in prompt


def test_inline_tells_the_customer_to_start_a_fresh_chat(vendor_id):
    """A customer who reopened an old chat for the same product got a blend of
    both: answers from days earlier and questions from a version of the intake
    the bank had since edited. Nothing in the conversation makes that visible, so
    the warning has to arrive before the first question rather than after."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert FRESH_CHAT_NOTE in prompt
    assert "disregard anything you remember" in prompt.lower()
    # With section 1, not buried at the end where it arrives too late to act on.
    assert prompt.index(FRESH_CHAT_NOTE) < prompt.index(REVIEW_THEN_PDF)


def test_the_customer_is_asked_to_disregard_an_old_chat_not_delete_it(vendor_id):
    """Deleting is a bigger ask than the problem needs, and one the customer may
    not want to grant — their earlier chat is their record of it. Ignoring the
    old thread fixes the same thing, because the damage comes from carrying it
    forward rather than from its existing."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert "disregard those previous conversations" in prompt
    assert "delete that conversation" not in prompt


# ---- the wording a vendor may replace -------------------------------------

def test_a_vendors_wording_replaces_ours_in_the_link(vendor_id):
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_prompt_texts(sid, {"pacing": "Ask them slowly. They are on a phone."})

    assert "Ask them slowly. They are on a phone." in skills.get_skill(sid)["entry_prompt"]


def test_a_paragraph_nobody_edited_still_follows_our_copy(vendor_id):
    """The reason the editor saves paragraphs rather than the whole prompt. A
    vendor who rewrote the pacing has not opted out of every future correction
    we make to the sentence about PDFs."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_prompt_texts(sid, {"pacing": "Ask them slowly."})
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert PDF_TRIGGER in prompt
    assert SECTION_CHECK in prompt
    assert FRESH_CHAT_NOTE in prompt


def test_clearing_a_box_puts_our_wording_back(vendor_id):
    """Blank means "use theirs", not "say nothing here". A vendor who empties the
    pacing box has not decided the assistant should ask all four sections at
    once, and that failure would surface in a customer's chat days later."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_prompt_texts(sid, {"pacing": "Ask them slowly."})
    skills.set_prompt_texts(sid, {"pacing": ""})
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert "Ask them slowly." not in prompt
    assert "one at a time" in prompt


def test_the_guardrail_survives_every_edit(vendor_id):
    """Everything a vendor can reach here is instruction. The one sentence that
    is an obligation is not on the list, and no key they can send reaches it."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_prompt_texts(sid, {k: "gone" for k in ("fresh", "pacing", "section_check",
                                                      "summary", "pdf")})

    assert GUARDRAIL in skills.get_skill(sid)["entry_prompt"]


def test_an_unknown_key_never_reaches_the_customer(vendor_id):
    """The prompt is compiled, and this is the one seam we left in it. A key we
    did not put there is a bug or a probe, and either way it should not be able
    to put text in front of a customer."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_prompt_texts(sid, {"guardrail": "Rates start at 4%."})

    assert "Rates start at 4%." not in skills.get_skill(sid)["entry_prompt"]


def test_the_questions_stay_the_vendors_own_after_a_rewrite(vendor_id):
    """The editable paragraphs are the instructions around the intake. The intake
    itself is edited as sections, and nothing here may quietly drop it."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_prompt_texts(sid, {"fresh": "Hi.", "pacing": "Go."})
    skill = skills.get_skill(sid)

    for question in skill["sections"][0]["asks"]:
        assert question in skill["entry_prompt"]


def test_rewriting_the_prompt_is_closed_to_strangers(vendor_id):
    """This endpoint writes the text a bank's customers are handed under the
    bank's name. An open one is a defacement tool."""
    from fastapi.testclient import TestClient

    from a2b.api.main import app

    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    response = TestClient(app).put(f"/api/skills/{sid}/prompt-texts",
                                   json={"pacing": "Rates start at 4%."})

    assert response.status_code == 401
    assert "Rates start at 4%." not in skills.get_skill(sid)["entry_prompt"]


def test_editing_one_paragraph_leaves_the_others_alone(vendor_id):
    """Saving is a merge. The console sends every box, but a call that carries
    one key is not an instruction to reset the rest."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_prompt_texts(sid, {"pacing": "Slowly."})
    skills.set_prompt_texts(sid, {"summary": "Read it all back."})
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert "Slowly." in prompt and "Read it all back." in prompt


def test_no_customer_facing_prompt_mentions_the_connector(vendor_id):
    """Out of scope for the pilot. Naming a tool the customer has not got is how
    #27 happened — the assistant stalled on its absence, apologised, then
    invented a schema. Connector mode itself is untouched and still selectable;
    it is simply not what anyone is handed."""
    from a2b.compiler import compile_full_entry_prompt, compile_short_entry_prompt

    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skill = skills.get_skill(sid)

    reachable = [skill["entry_prompt"]]
    for mode in ("staged", "doc"):
        skills.set_entry_mode(sid, mode)
        reachable.append(skills.get_skill(sid)["entry_prompt"])
    reachable.append(compile_full_entry_prompt(
        vendor_name="V", skill_name=skill["name"], sections=skill["sections"],
        vendor_slug="v", skill_slug="s"))
    reachable.append(compile_short_entry_prompt(
        vendor_name="V", skill_name=skill["name"],
        section_names=[s["name"] for s in skill["sections"]],
        workflow_url="https://example.com/x.md", vendor_slug="v", skill_slug="s"))

    for prompt in reachable:
        lowered = prompt.lower()
        assert "connector" not in lowered, prompt[:120]
        assert "lenni_start_session" not in lowered


def test_connector_mode_is_still_there_for_when_it_comes_back(vendor_id):
    """Removed from the prompts, not from the product. A vendor who selects it
    must still get the connector interview rather than a broken skill."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_entry_mode(sid, "connector")
    assert "lenni_start_session" in skills.get_skill(sid)["entry_prompt"]


def test_inline_still_paces_the_interview(vendor_id):
    """Handing over the whole intake means the assistant can see the last question
    before it asks the first, and left alone it will batch them or race. The
    pacing instructions are what buys back the one-at-a-time conversation."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    prompt = skills.get_skill(sid)["entry_prompt"]
    lowered = prompt.lower()

    assert "step by step" in lowered
    assert "one at a time" in lowered
    assert "do not show me a later section before we reach it" in lowered
    assert SECTION_CHECK in prompt


def test_inline_ends_on_the_pdf_instruction(vendor_id):
    """The last thing the customer is told has to be how to finish, and the
    assistant has to know not to produce the document before it is asked. Both
    have gone missing in live runs, which is why they are asserted rather than
    trusted to the prose."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert REVIEW_THEN_PDF in prompt
    assert PDF_TRIGGER in prompt
    assert "do not produce it before i ask" in prompt.lower()
    # The summary comes first, or the customer is asked to approve a package
    # they have not been shown.
    assert prompt.index("summarise the whole package") < prompt.index(REVIEW_THEN_PDF)


def test_inline_carries_the_closing_message_the_vendor_wrote(vendor_id):
    """Nothing is fetched in this mode, so anything left out of the opening
    message is never said. The closing message used to be left out, and the
    conversation ended on a downloaded PDF and silence — at exactly the moment
    the customer needs to be told where to send it."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.connect_convey(sid, "https://bluebonnet.convey.lenni.io")
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert "your package is complete and validated" in prompt
    assert "https://bluebonnet.convey.lenni.io" in prompt


def test_the_closing_message_waits_for_the_pdf(vendor_id):
    """Ordering is the requirement, not just presence. The summary the customer
    approves has to end on the PDF offer and nothing else, so the delivery
    details cannot ride along with it — they are what comes after."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.connect_convey(sid, "https://bluebonnet.convey.lenni.io")
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert "After the PDF exists — and only then" in prompt
    assert prompt.index(REVIEW_THEN_PDF) < prompt.index("After the PDF exists")
    # And the delivery text itself must sit after the PDF instruction, or an
    # assistant reading top to bottom will offer the secure link too early.
    assert prompt.index(PDF_TRIGGER) < prompt.index("convey.lenni.io")


def test_changing_the_destination_recompiles_the_link(vendor_id):
    """The closing message now travels inside the prompt, which puts the
    destination on the same footing as a question. A vendor who pastes their
    secure link an hour after publishing would otherwise keep handing out a link
    telling customers to use the old one, and it would keep working quietly."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_destination(sid, {"mode": "email", "email": "old@bank.example"})
    assert "old@bank.example" in skills.get_skill(sid)["entry_prompt"]

    skills.set_destination(sid, {"mode": "email", "email": "new@bank.example"})
    prompt = skills.get_skill(sid)["entry_prompt"]
    assert "new@bank.example" in prompt
    assert "old@bank.example" not in prompt


def test_an_unconfigured_destination_says_nothing_to_the_customer(vendor_id):
    """With no destination set, the link line reads "connect Convey in the
    destination settings" — right for the vendor's console, and an instruction
    to the borrower to configure our software once the closing message started
    travelling inside the prompt. Publishing is blocked without a destination,
    so this only bites on drafts, and drafts are what gets demoed."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert "destination settings" not in prompt
    # The rest of the closing message still travels — only the line naming a
    # destination that does not exist yet is held back.
    assert "your package is complete and validated" in prompt


def test_the_guardrail_is_not_swallowed_by_the_quoted_closing(vendor_id):
    """Everything after "word for word" is quoted material, and an assistant has
    no way to tell where the quote ends. Below it, the compliance line read as
    the vendor's own last sentence."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    prompt = skills.get_skill(sid)["entry_prompt"]

    assert prompt.index(GUARDRAIL) < prompt.index("word for word:\n\n")


def test_a_full_intake_still_fits_in_a_start_link(vendor_id):
    """The prompt has to survive percent-encoding into a URL, and the ceiling is
    the receiving end rather than the browser: a request line much over 8KB is
    refused by common front-end defaults, which looks to the customer like a
    broken button.

    Carrying the closing message pushed the largest intake to within a few
    hundred characters of that, so this is a tripwire rather than a formality.
    If it fails, the honest fixes are to cut our own wording or move the vendor
    to the connector — not to raise the number.
    """
    from urllib.parse import quote

    from a2b.links import MAX_QUERY

    sid = skills.seed_skill_from_template(vendor_id, "uw-cre-owner-occupied")
    skills.connect_convey(sid, "https://bluebonnet.convey.lenni.io")
    encoded = len(quote(skills.get_skill(sid)["entry_prompt"], safe=""))

    assert encoded <= MAX_QUERY, (
        f"the largest intake encodes to {encoded}, over the {MAX_QUERY} ceiling — "
        f"the start link will silently fall back to the short prompt")


def test_doc_handoff_carries_no_questions_and_demands_nothing(vendor_id):
    """The mode the product is sold on. It has to survive a laptop where nobody
    has installed anything, which is exactly what connector-only cannot do."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_entry_mode(sid, "doc")
    skill = skills.get_skill(sid)
    prompt = skill["entry_prompt"]

    assert "context.md" in prompt and "llms.txt" in prompt
    # One address, because the guide it points at carries the whole interview.
    assert "will not need to fetch anything else" in prompt
    for section in skill["sections"]:
        for question in section["asks"]:
            assert question not in prompt, f"leaked into the link: {question!r}"

    # It used to close by offering the connector as an upgrade. Removed with the
    # rest of the connector copy: an assistant that reads about a tool it has not
    # got tends to raise it with the customer rather than ignore it.
    lowered = prompt.lower()
    assert "connector" not in lowered
    assert "stop and tell me" not in lowered


def test_doc_handoff_tells_a_failed_fetch_to_own_up_rather_than_improvise(vendor_id):
    """This mode's whole risk is an assistant that did not fetch and improvised
    anyway. It used to be told to quote a code as proof, which asked the customer to
    police it and put our plumbing in their chat; the server logs the read instead.
    What still has to be in the prompt is the instruction to admit a failure."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_entry_mode(sid, "doc")
    prompt = skills.get_skill(sid)["entry_prompt"].lower()

    assert "if you cannot open the links, say so plainly" in prompt
    assert "how intakes like this usually go" in prompt
    assert "no need to tell me what you read" in prompt
    assert "confirmation code" not in prompt


def test_entry_prompt_does_not_tell_the_ai_to_dial_an_endpoint(vendor_id):
    """Naming tools is fine; naming a URL to connect to is not.

    No consumer terminal can attach to an MCP server from an address in prompt
    text — it has to be registered as a connector first, and asking anyway
    produced an apology and a guessed schema. The prompt now asks the assistant
    to *use* the connector if it already has one, which is a different thing.
    """
    prompt = skills.get_skill(
        skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm"))["entry_prompt"]
    assert "WebMCP" not in prompt
    assert "/mcp?vendor=" not in prompt


def test_the_prompt_leads_with_the_work_rather_than_the_plumbing(vendor_id):
    """Both prompts that used to open by naming tools are gone: the connector is
    out of scope for the pilot, so the first thing the customer's assistant reads
    is the vendor's first section. Covered as a property because the failures
    here have all been an assistant negotiating with our plumbing in front of the
    customer instead of asking a question."""
    skill = skills.get_skill(
        skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm"))
    prompt = skill["entry_prompt"]

    assert prompt.index("Section 1 of") < prompt.index(SECTION_CHECK)
    for question in skill["sections"][0]["asks"]:
        assert question in prompt


def test_a_prompt_that_names_an_address_makes_it_fetchable(vendor_id):
    """A bare `host/path` was being compiled in, which an agent reads as prose.

    Only the modes that send the AI somewhere are covered, which is now the
    minority of them — inline names no address at all, and that is the point of
    it rather than an omission.
    """
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    for mode in ("staged", "doc"):
        skills.set_entry_mode(sid, mode)
        prompt = skills.get_skill(sid)["entry_prompt"]
        assert "https://" in prompt or "http://" in prompt, mode

    skills.set_entry_mode(sid, "staged")
    assert "/start/multifamily-perm/2.md" in skills.get_skill(sid)["entry_prompt"]


def test_entry_prompt_always_carries_the_guardrail(vendor_id):
    sid = skills.seed_skill_from_template(vendor_id, "uw-consumer-auto")
    prompt = skills.get_skill(sid)["entry_prompt"]
    assert "not a credit decision" in prompt
    assert "not a rate quote" in prompt


def test_reordering_sections_recompiles_the_entry_prompt(vendor_id):
    """The prompt is a snapshot of the config taken at save time, so an edit that
    does not recompile ships yesterday's running order to every customer who
    clicks after it."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    assert "Section 1 of 3: Collateral" in skills.get_skill(sid)["entry_prompt"]

    second = skills.get_skill(sid)["sections"][1]
    skills.move_section(second["id"], -1)

    prompt = skills.get_skill(sid)["entry_prompt"]
    assert "Section 1 of 3: Deal" in prompt
    assert "Section 2 of 3: Collateral" in prompt


def test_reordering_changes_which_section_a_staged_link_hands_over(vendor_id):
    """Sharper in staged mode, where a reorder changes *which* section the
    customer is handed at the click rather than only the running order."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    skills.set_entry_mode(sid, "staged")

    second = skills.get_skill(sid)["sections"][1]
    skills.move_section(second["id"], -1)

    prompt = skills.get_skill(sid)["entry_prompt"]
    assert "Section 1 of 3: Deal" in prompt
    assert "Collateral" not in prompt


def test_recompiling_everything_refreshes_stale_prompts(vendor_id):
    """Prompts compiled by an older version, or against a hostname that has since
    moved, have to be rebuilt — otherwise a skill published last week keeps
    handing out an address that no longer resolves."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    from a2b.db import cursor
    with cursor() as cur:
        cur.execute("UPDATE vendor_skill SET entry_prompt=? WHERE id=?",
                    ("stale: connect over WebMCP at api.old-host.example/mcp", sid))
    assert "old-host" in skills.get_skill(sid)["entry_prompt"]

    assert skills.recompile_all() >= 1
    prompt = skills.get_skill(sid)["entry_prompt"]
    assert "old-host" not in prompt
    assert "Collateral" in prompt


def test_editing_a_question_reaches_the_customers_ai(vendor_id):
    """Wording lives in the prompt now, so an edit that does not recompile ships
    the old question to every customer who clicks after it."""
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    section = skills.get_skill(sid)["sections"][0]
    skills.update_section(section["id"], asks=["What is the roof's remaining life?"])

    prompt = skills.get_skill(sid)["entry_prompt"]
    assert "What is the roof's remaining life?" in prompt


def test_adding_a_section_lengthens_the_run_the_customer_is_promised(vendor_id):
    """A section added at the end is not in the opening message — only section 1
    is. What must change is the count, because that is the customer's sense of how
    far they have to go, and a stale one is a broken promise."""
    from a2b.compiler import render_workflow
    sid = skills.seed_skill_from_template(vendor_id, "uw-multifamily-perm")
    before = len(skills.get_skill(sid)["sections"])
    assert f"Section 1 of {before}" in skills.get_skill(sid)["entry_prompt"]

    skills.add_library_section(sid, "banking", 1)  # "Deposit relationship"
    skill = skills.get_skill(sid)

    assert f"Section 1 of {before + 1}" in skill["entry_prompt"]
    assert "Deposit relationship" in render_workflow(skill["sections"])


def test_produced_sentence_follows_the_output_toggles():
    dest = normalize_destination({"outputs": {"pdf": True, "md": False, "doclist": False},
                                  "mode": "link", "link": "https://example.com/upload"})
    fp = compile_final_prompt(dest=dest, vendor_name="Bluebonnet")
    assert "PDF package" in fp.produced
    assert "markdown" not in fp.produced
    assert "required-documents list" not in fp.produced


def test_turning_every_output_off_says_so_rather_than_promising_files():
    dest = normalize_destination({"outputs": {"pdf": False, "md": False, "doclist": False}})
    fp = compile_final_prompt(dest=dest, vendor_name="Bluebonnet")
    assert "no files were configured" in fp.produced


def test_vendor_edits_voice_but_cannot_edit_the_wiring():
    """Editable voice over locked wiring: the vendor's words survive, the
    produced-outputs sentence and the delivery link stay compiled."""
    dest = normalize_destination({
        "mode": "link",
        "link": "https://bluebonnet.example/upload",
        "outputs": {"pdf": True, "md": False, "doclist": False},
        "texts": {"open": "All done — nice work.", "close": "Talk soon.",
                  "deliver": {"link": "Pop it up at the link below."}},
    })
    fp = compile_final_prompt(dest=dest, vendor_name="Bluebonnet")

    assert fp.opening == "All done — nice work."
    assert fp.delivery == "Pop it up at the link below."
    assert fp.sign_off == "Talk soon."
    # Not vendor-authored: derived from settings every time.
    assert "PDF package" in fp.produced
    assert "https://bluebonnet.example/upload" in fp.link_line
    assert set(fp.locked) == {"produced", "link_line"}


def test_changing_the_delivery_mode_reflows_the_link_line():
    base = {"outputs": {"pdf": True, "md": True, "doclist": True}}
    email = compile_final_prompt(
        dest=normalize_destination({**base, "mode": "email", "email": "loans@bank.com"}),
        vendor_name="Bank")
    assert "loans@bank.com" in email.link_line
    assert "not a secure channel" in email.delivery

    convey = compile_final_prompt(
        dest=normalize_destination({**base, "mode": "convey", "connected": True,
                                    "link": "https://b.convey.lenni.io"}),
        vendor_name="Bank")
    assert "https://b.convey.lenni.io" in convey.link_line


def test_a_skill_with_nowhere_to_deliver_cannot_publish():
    ok, blockers = can_publish(normalize_destination({"mode": "convey", "connected": False}))
    assert ok is False and blockers

    ok, _ = can_publish(normalize_destination(
        {"mode": "link", "link": "https://example.com/u", "outputs": {"pdf": True}}))
    assert ok is True
