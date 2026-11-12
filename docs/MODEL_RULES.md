# The locked model rules

Seven rules from the dev brief, each with where it lives in this codebase and
what breaking it would cost. They are law, not preferences: a change that
violates one is a product decision, not a refactor, and belongs in the OS before
it belongs in a pull request.

---

## 1. Template → your version

Seeding **copies** the template at version T and pins it. Lenni ships T+1 and the
vendor's skill does not move. No auto-sync, no force override. The UI shows
pinned versus currently available. Updating is vendor-initiated only.

**Where:** `a2b/skills.py::seed_skill_from_template` takes the deep copy and
writes `seeded_from_template_version`. `adopt_template_version` is the only path
to newer content, and nothing calls it except a vendor action.
`template_diff` shows what would change first, including which of the vendor's
own edits would be lost.

**Cost of breaking it:** you rewrite, without asking, the words a bank has
approved for talking to its own customers. There is no user action to point at
afterwards and no way for them to notice until a customer sees it. This is the
most damaging bug available in this codebase, which is why
`tests/test_pinning.py` is the largest test file.

---

## 2. Suggest, confirm, seed

Website ingest *proposes* an industry and product matches. The vendor *confirms*
every one. Nothing is ever auto-bound.

**Where:** `ingest_match.confirmed` defaults to `0` and only
`ingest.confirm_matches` sets it, driven by an explicit list of match ids.
Scanning a site creates zero skills — `test_matcher.py::test_nothing_is_bound_until_a_human_confirms`
asserts exactly that.

**Cost of breaking it:** ingest is the first impression and the live demo weapon.
A single wrong auto-binding turns "look how well it read your site" into "look
what it did to my site without asking".

---

## 3. Sections replace legs

The runtime field is `section_status`, formerly `leg_status`. Each completed
section calls back over the connector for the next. The vendor never sees this
plumbing — they see "moves on to section 2 — Deal".

**Where:** `a2b/sessions.py`. `complete_section` folds completion and advancement
into one call so a caller cannot record progress and forget to advance. Status is
keyed by section id rather than index, so a vendor editing a skill mid-run cannot
leave a customer pointed at the wrong section.

**Cost of breaking it:** the vendor-facing abstraction leaks, and the editor
starts looking like a schema instead of a conversation.

---

## 4. Two publish outputs, always

One link per skill for humans, and the agent manifest for AIs. Both doors reach
the same skill and the same destination.

**Where:** `a2b/links.py` builds the universal start link, `a2b/manifest.py`
builds `llms.txt` / `llms.md` / the `/agents` page. Publishing regenerates both.
Drafts appear in neither.

**Cost of breaking it:** the two doors drift, and an agent runs a version of the
service the vendor does not think is live.

---

## 5. Secure delivery is the terminal step

Convey is the included default, not a requirement. A vendor may configure their
own secure link, or email with an honesty warning attached. The skill's config
carries the destination.

**Where:** `a2b/destination.py` and the `destination` blob on `vendor_skill`.
`compiler.can_publish` refuses to publish a skill with no working destination.

**Note on a deliberate divergence from the demo:** the click-through lets you
publish a skill whose Convey is not connected, and it looks fine. Here that is
refused. A published skill with nowhere to deliver strands a real customer at the
last step, after they have done all the work — the worst possible moment to
discover a configuration gap.

---

## 6. The guardrail rides every session

Introductory only. Never a credit decision, never a rate quote. The vendor
decides everything.

**Where:** `config.GUARDRAIL` is a single constant, injected into every compiled
entry prompt, returned in `lenni_start_session`, and written into both manifest
forms. It is not configurable per vendor, and there is no code path that omits
it.

**Cost of breaking it:** this is the compliance surface. It is one constant in one
place specifically so that no one can turn it off for one vendor as a favour.

---

## 7. One link, every AI

MCP is the how: publish once, run in Claude, ChatGPT, Copilot or Grok. The
universal link (`vendor.com/start/<slug>`) shows a terminal chooser and remembers
the pick. Branded deep links are pre-routed versions of it.

Two routes reach the same tools, and the difference is worth keeping straight.
A customer who has registered the connector gets the workflow from the server as
tool calls over `a2b/mcp/transport.py`. A customer who has not gets the staged
prompt, which carries one section and the address of the next. The first is the
product; the second is what makes the link work for everyone else today.

This is not WebMCP. WebMCP registers tools in a page for an agent running inside
the browser, which no consumer terminal ships enabled — and it cannot survive the
customer leaving the bank's site for Claude, which is the entire journey here.

**Where:** `a2b/links.py` and `GET /v/{vendor}/start/{skill}`.

**Note on a deliberate divergence from the demo:** the demo shows five deep-link
buttons that each pre-load the prompt into a named assistant. That pattern does
not universally work — `claude.ai` does not accept a `?q=` parameter, and the
brief flags this. Each terminal here carries a `supported` flag. Where a real
deep link exists it is used; where one does not, the chooser copies the prompt
instead. Shipping five buttons of which two silently fail would put the failure
in front of the customer at the exact moment the vendor is proudest of the link.
