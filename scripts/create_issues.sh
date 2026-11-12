#!/usr/bin/env bash
#
# Create the project's GitHub issues.
#
# Completed work is filed as an issue and immediately closed, so the repository
# carries a record of what was built and why rather than only what is left.
# Remaining work stays open with a priority label.
#
# Requires: gh, authenticated (`gh auth login`).
# Idempotent: an issue whose exact title already exists is skipped.
#
#   ./scripts/create_issues.sh              # create everything
#   ./scripts/create_issues.sh --dry-run    # print what would be created
#
set -euo pipefail
cd "$(dirname "$0")/.."

REPO="ALL-Dunkin/A2B-system"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

command -v gh >/dev/null || { echo "gh not installed: brew install gh"; exit 1; }
if [ "$DRY" -eq 0 ]; then
  gh auth status >/dev/null 2>&1 || { echo "Not authenticated. Run: gh auth login"; exit 1; }
fi

# --------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------
label() {  # name colour description
  [ "$DRY" -eq 1 ] && { echo "label: $1"; return; }
  gh label create "$1" --repo "$REPO" --color "$2" --description "$3" --force >/dev/null 2>&1 || true
}

echo "Labels…"
label "done"            "0e8a16" "Built and verified"
label "p0-blocker"      "b60205" "Blocks deployment"
label "p1"              "d93f0b" "Needed for a real pilot"
label "p2"              "fbca04" "Wanted, not urgent"
label "area:tenancy"    "1d76db" "Vendors, skills, version pinning"
label "area:compiler"   "1d76db" "Prompt compilation"
label "area:publish"    "1d76db" "Links and the agent manifest"
label "area:runtime"    "1d76db" "Sessions, chaining, MCP"
label "area:ingest"     "1d76db" "Website scanning and matching"
label "area:console"    "1d76db" "Vendor console"
label "area:content"    "1d76db" "Registry: products, templates, library"
label "area:infra"      "1d76db" "Deployment, storage, auth"
label "epic"            "5319e7" "Tracking issue"

# --------------------------------------------------------------------------
# Issues
# --------------------------------------------------------------------------
existing=""
if [ "$DRY" -eq 0 ]; then
  existing="$(gh issue list --repo "$REPO" --state all --limit 200 --json title --jq '.[].title')"
fi

issue() {  # title  labels  close?  body(stdin)
  local title="$1" labels="$2" close="$3" body
  body="$(cat)"
  if printf '%s\n' "$existing" | grep -Fxq "$title"; then
    echo "  skip (exists): $title"
    return
  fi
  if [ "$DRY" -eq 1 ]; then
    echo "── [$labels]${close:+ (close)} $title"
    return
  fi
  local url
  url="$(gh issue create --repo "$REPO" --title "$title" --label "$labels" --body "$body")"
  echo "  $url  $title"
  if [ -n "$close" ]; then
    gh issue close "$url" --repo "$REPO" \
      --comment "Delivered in the initial commit. Filed and closed to record the decision." >/dev/null
  fi
}

echo
echo "Issues…"

# ======================================================================
# EPIC
# ======================================================================
issue "Epic: A2B system — build status and roadmap" "epic" "" <<'EOF'
Tracking issue for the A2B build specified in `DEV-BRIEF-a2b-system.md` and
prototyped in `a2b-demo.html`.

## What A2B is

A service business (a **vendor**) turns each service on its website into a
**skill** that its customers' own AI can run. The customer works in their own
terminal, guided section by section on the vendor's rules, and the finished
package is delivered securely back to the vendor.

## Status

The skeleton is complete and verified. Every item in the brief's suggested build
order (section 7) is implemented, with 52 tests plus a headless-browser
walkthrough of the console.

**Done** — tenancy with version pinning, prompt compiler, publish pipeline,
agent manifest, section chaining over WebMCP, destination assembly, ingest
matcher, vendor console, content registry, test suite.

**Not done** — authentication (blocks any deployment), real Convey delivery,
outbound email, the FFIEC marketplace app, and registry breadth.

## Reading order for anyone new

1. `README.md` — architecture, data model, API, honest gaps
2. `docs/MODEL_RULES.md` — the seven locked rules and where each lives in code
3. `docs/GLOSSARY.md` — vocabulary; the OS copy is canonical
4. `./run.sh`, then walk the fifteen-minute loop in the README

## The rule that matters most

Rule 1, template → your version. Seeding copies a template at version T and pins
it; Lenni shipping T+1 must never move a vendor's skill. Breaking this silently
rewrites words a bank approved for talking to its own customers, with no user
action to point at afterwards. `tests/test_pinning.py` is the largest test file
for that reason.
EOF

# ======================================================================
# COMPLETED
# ======================================================================

issue "Tenancy: vendor, vendor_skill, and template version pinning" \
      "done,area:tenancy" "close" <<'EOF'
Build order item 1. Multi-tenant foundation with the pinning rule enforced.

## Delivered

- `vendor`, `vendor_member`, `vendor_skill`, `skill_section` tables (`a2b/db.py`)
- `seed_skill_from_template` deep-copies a template's sections and records
  `seeded_from_template_id` and `seeded_from_template_version`
- `create_custom_skill` for offerings with no template behind them
- `template_diff` shows what adopting a newer version would change, including
  which of the vendor's own edits would be lost
- `adopt_template_version` is the only path to newer content, and nothing calls
  it except an explicit vendor action
- Core sections can be edited but not deleted (409 from the API)

## Design decisions

**`seeded_from_template_version` is a stored string, not a foreign key.** A
foreign key creates a live relationship, which is precisely what pinning
forbids. The pin is a historical fact about a copy that already happened.

**Vendor slugs are deduplicated rather than unique-on-name.** Two banks called
"First National" is ordinary; failing signup over it would be absurd.

## Verified

`tests/test_pinning.py` — deep-copy semantics, two vendors staying independent,
a shipped template version not moving a seeded skill, explicit adoption keeping
vendor-added sections, adoption refused on custom skills.
EOF

issue "Prompt compiler: entry prompt and final prompt" \
      "done,area:compiler" "close" <<'EOF'
Build order item 2. Config compiles to prompt, so prompts never rot.

## Delivered

- `compile_entry_prompt` builds the text a start link carries into the
  customer's AI: vendor, skill, WebMCP endpoint, the ordered section walk, and
  the guardrail. Regenerated on every save.
- `compile_final_prompt` splits the closing message into editable vendor voice
  (opening, delivery instructions, sign-off) and locked compiled wiring (the
  produced-outputs sentence and the delivery link).
- `can_publish` / `delivery_warnings` gate publishing and surface honesty
  warnings in the console.

## Why the split

Editable voice over locked wiring is rule 1 in miniature. A vendor can never
accidentally promise a PDF they turned off, or paste a stale upload link into
their closing message — those sentences are derived from settings every time.

## Verified

`tests/test_compiler.py` — reordering a section regenerates the walk, the
produced sentence follows the output toggles, vendor overrides survive while
compiled parts do not, changing delivery mode reflows the link line, and a skill
with nowhere to deliver cannot publish.
EOF

issue "Publish pipeline: universal start link and terminal chooser" \
      "done,area:publish" "close" <<'EOF'
Build order item 2 (second half). Rule 4: two publish outputs, always.

## Delivered

- `universal_link` prefers the vendor's own domain, falling back to a
  Lenni-hosted path when no domain is on file
- `GET /v/{vendor}/start/{skill}` renders the chooser, remembers the customer's
  pick in localStorage, and 404s on a draft
- `publish_skill` refuses when there is no working destination
- `funnel_event` rows recorded on start-link views and every session step

## Deliberate divergence from the demo

The demo shows five deep-link buttons that each pre-load the prompt into a named
assistant. That does not universally work — `claude.ai` does not accept a `?q=`
parameter, which the brief itself flags. Each terminal in `a2b/links.py` carries
a `supported` flag: where a real deep link exists it is used, and where one does
not the chooser copies the prompt instead.

Shipping five buttons of which two silently fail would put the failure in front
of the customer at the exact moment the vendor is proudest of the link.

Establishing what actually routes is tracked separately in "Establish which
terminal deep links actually route".
EOF

issue "Agent manifest: llms.txt, llms.md and the /agents page" \
      "done,area:publish" "close" <<'EOF'
Build order item 3. A2B discovery — an agent reads one page instead of crawling
forty-two.

## Delivered

- `llms_txt` — short form, points at the long form, lists runnable services
- `llms_md` — what an agent reads before running something: how to engage, the
  section walk per service, the WebMCP endpoint, where a finished package goes
- `agents_page_data` — human-readable view that includes drafts, flagged as
  unrunnable
- `webmaster_packet` — the entire integration, two lines of HTML
- Regenerates on every publish; drafts appear in neither manifest form

## Why the agents page shows drafts but the manifest does not

A vendor looking at their own agent page should see what an agent is *not* being
shown. That is the clearest way to make publishing legible as a consent step
rather than a save button.

## Verified

`tests/test_manifest.py` — drafts omitted from both forms, publish/unpublish
round-trips, the guardrail present, the WebMCP endpoint correct.
EOF

issue "Section chaining over WebMCP" \
      "done,area:runtime" "close" <<'EOF'
Build order item 4. Rule 3: sections replace legs.

## Delivered

- `section_status` per session, plus `facts` and funnel events
- `complete_section` marks a section done and returns the next in one call
- Ten MCP tools in `a2b/mcp/server.py`, keeping the `lenni_` prefix
- Email-keyed customer profiles, portable across vendors
- `validate_package` reports gaps and the next question, never judging the deal
- `ToolContext.marketplace` — vendor matching is off inside a vendor's own skill

## Design decisions

**Status is keyed by section id, not index.** A vendor can add, remove or
reorder sections while a customer is mid-run. Index-keyed status would silently
point at the wrong section after any of those.

**Completion and advancement are one call.** A caller cannot record progress and
then forget to advance.

**Tool names keep the `lenni_` prefix.** The brief is explicit that these are the
same tools gaining tenancy, not replacements, so renaming would break the one
integration already in the wild for no gain.

## Verified

`tests/test_chaining.py` — draft skills unrunnable, the full walk, status
surviving a mid-run add and a mid-run delete, core deletion refused, profile
portability across two vendors.
EOF

issue "Destination: output assembly and delivery routing" \
      "done,area:runtime" "close" <<'EOF'
Build order item 5. Rule 5: secure delivery is the terminal step, Convey is the
default rather than a requirement.

## Delivered

- Markdown recap of every section, plus the captured facts
- Document list with per-item status, driven by the doc-checklist app
- Typeset PDF via ReportLab
- Three delivery modes: Convey, a vendor's own secure link, or email
- `delivery_instruction` returns a `secure` flag and an honest warning on email

## Design decisions

**ReportLab is optional.** Without it, `assemble()` produces the markdown and the
document list and notes that the PDF was skipped. A missing optional dependency
should degrade the output, not fail the customer's delivery at the last step.

**Publishing without a destination is refused.** The demo lets you publish with
Convey unconnected and it looks fine. A published skill with nowhere to deliver
strands a real customer after they have done all the work — the worst possible
moment to discover a configuration gap.
EOF

issue "Website ingest: scan, industry detection, product matching" \
      "done,area:ingest" "close" <<'EOF'
Build order item 6. Rule 2: suggest, confirm, seed — nothing is ever auto-bound.

## Delivered

- HTML scan pulling headings, navigation and body text (stdlib parser)
- Industry ranking from per-industry signal words
- Product matching against the registry's `site_phrases`, with an explaining
  phrase and a quote from the vendor's own page
- `ingest_run` / `ingest_match` with `confirmed` defaulting to 0
- `unmatched_demand` aggregates unmatched offerings across every scan — the
  template roadmap
- Offerings with no template become custom skills rather than being dropped

## Why the matcher is lexical, not model-based

Matching a page heading to a registry of known products is a bounded string
problem: small candidate set, domain-specific vocabulary, and `site_phrases`
already encodes the phrasings that occur in the wild. Lexical is free, instant,
deterministic and — most importantly — explainable. It can show the vendor which
phrase it matched, which is what makes the confirm step meaningful rather than a
rubber stamp on a black box.

If measurement later shows this failing on real sites, add a model, and measure
it against this baseline rather than against nothing.

## Two bugs found and fixed during the build

**A weak tagline was stealing products from real headings.** The h1 "Lending,
decided locally" brushed against "multifamily lending" and, being first on the
page, claimed the product — discarding the "Apartment & multifamily lending"
heading as a duplicate. Fixed by scoring everything before de-duplicating and
resolving collisions by best score.

**Matches carried only by generic words.** "Aircraft financing" matched
"Equipment Finance" on the shared word `financing`. Every product in an industry
was a weak match for every offering in it, because they all say "loans" or
"financing". Overlap consisting entirely of generic domain words now scores zero.

**Quotes borrowed the next service's description.** A short blurb caused the
quote picker to run past the following heading, so "Auto loans" was quoting the
Trust Services copy. Caught from a screenshot during the console walkthrough.
EOF

issue "Vendor console" "done,area:console" "close" <<'EOF'
Build order item 7. The demo's shape, wired to real endpoints throughout.

## Delivered

Home with URL paste, scan progress, industry confirm, match confirm, then the
workspace: services list, skill editor, apps, agent page, website preview,
sessions, team.

The editor reads top to bottom as the customer's journey — compiled entry
prompt, sections accordion with view and edit modes, Library with cross-industry
tabs, destination panel, publish rail with pinned-versus-available and start
links.

"Preview the customer run" executes the real MCP tools end to end and shows the
chaining.

Vanilla JS, no build step, no framework.

## Verified

`scripts/drive_console.py` drives it in headless Chrome and asserts at each step,
failing on any uncaught JavaScript error:

```
- industry suggested = Banking
- 7 offerings proposed, 6 at high confidence
- 6 draft skills created
- entry prompt compiled with the section walk
- core section delete refused
- destination set, publish unblocked
- skill published
- agent page regenerated
- customer run chained through every section
```
EOF

issue "Content registry: industries, products, templates, library, apps" \
      "done,area:content" "close" <<'EOF'
The Lenni-authored content the whole system reads from. Versioned with the code,
which is what lets a template carry a version and changelog that a vendor's
pinned copy can be compared against.

## Delivered

| File | Contains |
|---|---|
| `industries.yaml` | 18 industries; 5 with templates, 13 catalogued |
| `products.yaml` | 21 products with `site_phrases` |
| `core_sections.yaml` | Core section set per industry |
| `templates/*.yaml` | 18 versioned templates with changelogs |
| `library_sections.yaml` | 14 pre-drafted addable sections |
| `apps.yaml` | 8 apps, each flagged implemented or planned |

Templates compose from a core set plus `overrides` and `extra_sections`, so
adding a product is usually a dozen lines rather than a full section script.

## Note on site_phrases

Written in **vendor voice** — the words on a bank's own marketing pages — not
customer voice. The old `loan_products.yaml` keywords ("apartment", "warehouse
for my business") were written for a borrower typing into a chat box and match
marketing copy poorly. Both vocabularies are legitimate; they aim at different
text, and mixing them degrades both.

Breadth is tracked separately — see the registry expansion issue.
EOF

issue "Test suite and console walkthrough" "done,area:infra" "close" <<'EOF'
52 tests, plus an assertion-driven headless-browser walkthrough.

| File | Covers |
|---|---|
| `test_pinning.py` | Rule 1 — the most expensive rule to break |
| `test_compiler.py` | Prompt compilation, locked vs editable, publish gates |
| `test_chaining.py` | Sessions, mid-run edits, core protection, portability |
| `test_manifest.py` | Drafts omitted, publish round-trip, honest terminals |
| `test_matcher.py` | Extraction, detection, collisions, nothing auto-bound |
| `test_api.py` | Full journey over HTTP, then a customer run over MCP |

Tests are isolated by redirecting the data directory before `a2b.config`
resolves paths, with a fresh database per test, so ordering can never matter.

`scripts/drive_console.py` is what caught the third matcher bug — the wrong
quote was visible in a screenshot but passed every unit test.
EOF

# ======================================================================
# REMAINING
# ======================================================================

issue "Authentication and authorization" "p0-blocker,area:infra" "" <<'EOF'
**Nothing should be deployed anywhere reachable until this exists.**

There is currently no login, no session, and no authorization check. Any caller
can read or modify any vendor's skills, publish on their behalf, or read their
customers' session facts.

This was left out deliberately rather than half-built: a partial auth story
invites the assumption that something is protected when it is not.

## Scope

- Vendor login and session handling for the console
- Enforce the `vendor_member` roles that already exist in the schema:
  admin publishes, editor drafts, viewer reads
- Scope every `/api/*` route to the caller's vendor
- Decide the MCP authentication model — a customer's AI connects with no
  credential today, which is correct for a public start link but means
  `lenni_get_user_context` will return any profile given its email. That is the
  sharpest edge here and should be closed before any real customer data exists.
- Rate limiting on the public endpoints, particularly `/api/ingest/scan`, which
  makes an outbound request on demand

## Acceptance

- A request without a valid session cannot reach another vendor's data
- Role enforcement covered by tests
- `lenni_get_user_context` cannot enumerate profiles by guessing emails
EOF

issue "Port FFIEC bank matching as the fdic-bank-data app" "p1,area:runtime" "" <<'EOF'
`lenni_match_vendors` currently raises `not_implemented` rather than returning a
placebo result.

The FFIEC pipeline, the 351-bank dataset and the ranking logic live in
[FFIEC-Data-pull](https://github.com/ALL-Dunkin/FFIEC-Data-pull). The brief maps
`lenni_match_banks` onto two things here: the `fdic-bank-data` app, and the
marketplace path.

## Scope

- Port the matching logic and its data loading
- Register `fdic-bank-data` as implemented in `content/apps.yaml`
- Wire `lenni_match_vendors` to it for the marketplace path
- Decide how the bank dataset is distributed — it is deliberately excluded from
  both repositories

## The rule to preserve

Matching is **off** on a vendor's own machine. On a bank's own site the customer
works with that bank's products, not a comparison against its competitors.
`ToolContext.marketplace` already enforces this; keep it that way.
EOF

issue "Convey delivery integration" "p1,area:runtime" "" <<'EOF'
Connecting Convey currently stores a URL and routes the customer to it. There is
no API call into Convey, no upload, and no delivery receipt.

That is honest as far as it goes — the customer's AI does tell them where to
upload — but the vendor has no confirmation anything arrived, and the package
this system assembles never actually travels.

## Scope

- Push the assembled outputs into the vendor's Convey instance
- Delivery receipt back to the session, so `lenni_finish` can confirm rather
  than instruct
- Surface delivery status in the console's Sessions view
- Handle failure without stranding the customer at the last step

## Note

The same question applies to the `link` delivery mode. A Dropbox file request or
SharePoint folder cannot be pushed to in general, so that mode probably stays
instructional. Worth deciding explicitly rather than by omission.
EOF

issue "Outbound email: team invites and the email destination" "p1,area:infra" "" <<'EOF'
Two places currently record an intention without acting on it.

**Team invites** are written to `vendor_member` with status `invited`. No email
is sent, so an invited person has no way to know or to accept.

**The email delivery mode** stores an address and tells the customer's AI to
send there. This system does not send the package itself.

## Scope

- Pick a transport — SES is already in use elsewhere in the project
- Invite emails with an accept flow, which depends on the auth issue
- Decide whether the email destination means "we send it" or "we instruct the
  customer's AI to send it". Today it is the latter, and the console does not
  make that distinction clear to the vendor.

## Keep

The honesty warning on the email destination. It is not decoration — email is
genuinely the wrong channel for tax returns, and saying so is part of the
product's character.
EOF

issue "Expand the product registry to full breadth" "p1,area:content" "" <<'EOF'
The registry currently holds 21 products across 5 industries with templates,
plus 13 industries catalogued without them.

The brief describes 39 banking products in three tiers, and roughly 200 across
18 industries.

This is content work, not code work — the loader, the composition model and the
matcher all handle more rows without modification.

## Scope

- Reconcile against the OS registry (`tables/`), which wins on taxonomy
- Fill banking out to 39 with tier assignments
- Write `site_phrases` for each, in vendor voice
- Extend templates beyond the five industries that have them
- Feed `GET /api/ingest/demand` back into prioritisation — unmatched offerings
  aggregated across every scan are a real signal about which template to write
  next

## Watch for

Adding products without `site_phrases` makes them invisible to ingest. Adding
`site_phrases` that are too generic makes every product match everything — the
generic-word rule in `a2b/ingest.py` guards against the worst of it, but the
phrases themselves need to be discriminating.
EOF

issue "Multi-page ingest: crawl beyond a single URL" "p1,area:ingest" "" <<'EOF'
`ingest.fetch` retrieves the URL given and parses that one document. It does not
follow navigation, crawl service pages, or render JavaScript.

Consequences today: a single-page app scans as nearly empty, and a bank whose
homepage links to separate lending pages yields only whatever the homepage
happens to mention.

Since "paste your URL, see your machine" is the entire sales motion, scan
quality is close to a product-market fit question rather than a nice-to-have.

## Scope

- Follow same-domain navigation links to a bounded depth
- Prioritise likely service pages (`/loans`, `/business`, `/services`)
- Decide on JavaScript rendering — headless Chrome is already a dependency of
  the console test harness
- Respect `robots.txt` and rate-limit politely
- Keep the per-offering source URL so the confirm screen can cite the page it
  came from

## Do not lose

The quote beside each suggestion. Being able to see their own words next to our
proposal is what makes confirmation meaningful.
EOF

issue "Establish which terminal deep links actually route" "p1,area:publish" "" <<'EOF'
The brief lists this as unresolved: deep links are placeholder patterns, and
`claude.ai` does not accept a `?q=` parameter.

`a2b/links.py` currently marks Claude unsupported and ChatGPT, Grok and Copilot
supported, with the universal chooser copying the prompt where no real link
exists. Those flags are an informed guess, not a tested matrix.

## Scope

- Test each assistant, web and desktop, and record what actually works
- Investigate desktop URL schemes as an alternative to web query parameters
- Consider a short redirect service — a start link that resolves server-side to
  whatever currently works for the chosen terminal, so this stops being a
  deploy-time constant
- Update `TERMINALS` with measured results

## Constraint

Anything that cannot be routed must keep saying so. A button that silently fails
does so in front of the customer at the moment the vendor is proudest of it.
EOF

issue "Migrate from SQLite to Postgres" "p2,area:infra" "" <<'EOF'
SQLite is a skeleton choice and fine for a single process. Real multi-tenancy
wants Postgres.

The schema in `a2b/db.py` ports directly — the only SQLite-specific pieces are
the `PRAGMA` statements and the connection handling.

## Scope

- Port the schema and add proper migrations
- Replace the raw connection helper with a pool
- Revisit the JSON columns (`destination_json`, `facts_json`,
  `section_status_json`); `jsonb` makes several of them queryable, which the
  analytics issue would benefit from
- Keep SQLite working for local development and tests if it is cheap to do so

Not urgent until there are concurrent vendors, but it gets more expensive the
longer it waits.
EOF

issue "Funnel analytics: aggregate the events into something readable" \
      "p2,area:console" "" <<'EOF'
`funnel_event` rows are written on every meaningful action — start-link views,
session starts, section completions, deliveries. Nothing aggregates them beyond
a count per event type.

The data to answer "where do customers drop out of my skill" is already being
collected; nobody can see it.

## Scope

- Per-skill funnel: link views, sessions started, section-by-section drop-off,
  completions, deliveries
- Surface it in the console's Sessions view
- Time windows and comparison between skills
- Decide retention — sessions carry customer-supplied facts, so this is a
  privacy question, not only a storage one
EOF

issue "AI assist for section editing" "p2,area:console" "" <<'EOF'
The brief notes a free-form AI assistant was tried in the demo and pulled:
*editing something you can't see felt wrong.* When it returns it should be
**propose → show the exact diff → Apply or Discard** — the same suggest-confirm
philosophy as ingest.

## Scope

- Vendor describes a change in natural language
- Generate the proposed section content
- Show a precise diff against the current version
- Apply or discard; nothing lands without an explicit click
- Mark touched sections `edited`, which the schema already supports

## The rule this must not break

Rule 2 in spirit. If AI assist ever writes directly to a section, the vendor no
longer knows what their machine says to their customers — which is the entire
thing this product sells.
EOF

issue "id-verify (Plaid) app" "p2,area:runtime" "" <<'EOF'
Registered in `content/apps.yaml` as wave 2, `implemented: false`. Referenced by
the borrower section of several banking templates.

Identity verification belongs inside the borrower section, before any documents
move.

## Scope

- Plaid integration, or an equivalent provider
- Define how an app actually executes at runtime. This is the broader gap: apps
  are currently declarative tags on a section that the customer's AI is told
  about, with no execution model behind them. `doc-checklist` works because its
  logic lives in `validate_package`; `id-verify` needs something new.
- Verification result recorded on the session, not in the transcript

## Worth deciding first

Whether apps run server-side as MCP tools the customer's AI calls, or as
instructions the AI carries out itself. That choice shapes every app after this
one.
EOF

issue "Notify vendors when a template version they are pinned to is superseded" \
      "p2,area:tenancy" "" <<'EOF'
`template_status.update_available` is computed and shown in the editor, and
`template_diff` explains what adopting would change. But a vendor only learns a
new version exists by opening that skill.

## Scope

- Surface pending updates on the services list, not only inside the editor
- Optional notification when a template a vendor is pinned to ships a version
- Show the changelog entry, which templates already carry
- Consider a per-vendor summary — "3 of your 6 skills have updates available"

## The rule this must not break

Rule 1. Notification only. No auto-adopt, no default-on, no nagging that makes
staying pinned feel like negligence. A vendor who never updates is exercising
the model correctly, not falling behind.
EOF

echo
echo "Done."
[ "$DRY" -eq 1 ] && echo "(dry run — nothing was created)"
