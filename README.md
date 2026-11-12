# Lenni A2B — Agent to Business

A working skeleton of the A2B system described in `DEV-BRIEF-a2b-system.md` and
prototyped in `a2b-demo.html`.

A2B answers one question: **how does your customer get from their AI to your
business?** A service business — a **vendor** — turns each service on its website
into a **skill** that its customers' own AI can run. The customer works in their
own terminal (Claude, ChatGPT, Copilot, Grok — their tokens), guided section by
section on the vendor's rules, and the finished package is delivered securely
back to the vendor.

The demo is a click-through spec: one HTML file, canned data, no persistence.
This is the machinery behind those screens — a real data model, real tenancy and
version pinning, a real prompt compiler, a real MCP runtime, and a console wired
to all of it.

---

## Contents

- [Quick start](#quick-start)
- [The fifteen-minute loop](#the-fifteen-minute-loop)
- [What is built, and what is not](#what-is-built-and-what-is-not)
- [Architecture](#architecture)
- [The data model](#the-data-model)
- [Module map](#module-map)
- [The seven locked rules](#the-seven-locked-rules)
- [The content registry](#the-content-registry)
- [The MCP tool surface](#the-mcp-tool-surface)
- [HTTP API](#http-api)
- [Where this deliberately diverges from the demo](#where-this-deliberately-diverges-from-the-demo)
- [How the existing Lenni build maps in](#how-the-existing-lenni-build-maps-in)
- [Testing](#testing)
- [Deployment notes](#deployment-notes)
- [Glossary](#glossary)

---

## Quick start

Python 3.9 or newer. No database server, no external services, no API keys.

```bash
git clone https://github.com/ALL-Dunkin/A2B-system.git
cd A2B-system
./run.sh
```

`run.sh` creates a virtualenv, installs dependencies, seeds the Bluebonnet
Community Bank scenario from the demo, and starts the server:

| | |
|---|---|
| Vendor console | http://127.0.0.1:8000/console/ |
| API documentation | http://127.0.0.1:8000/docs |
| MCP tool surface | http://127.0.0.1:8000/mcp/tools |
| Health | http://127.0.0.1:8000/health |

Manually, if you prefer:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python scripts/seed_demo.py --reset      # optional sample data
uvicorn a2b.api.main:app --reload
```

### The seed script

`scripts/seed_demo.py` reproduces the demo's starting point: Bluebonnet
Community Bank, six offerings scanned off a website fixture, five matched to
templates and one (Trust Services) with no template that becomes a custom skill.
It uses a built-in HTML fixture, so it needs no network.

```
$ python scripts/seed_demo.py --reset
vendor: Bluebonnet Community Bank  (bluebonnet-community-bank)
industry suggested: banking
scan found 7 offerings:
  High      Apartment & multifamily lending    -> Multifamily Permanent
  High      Auto loans                         -> Consumer Auto
  High      Business loans & lines of credit   -> General C&I
  High      Commercial real estate loans       -> Owner-Occupied CRE
  High      Home construction loans            -> Residential Construction (1-4)
  High      Trust services                     -> Trust Services
  No match  Lending, decided locally.          -> — no template —
```

That last row is the page's h1 tagline. It is shown, unticked, and the vendor
ignores it — which is the suggest-and-confirm model working, not a bug.

---

## The fifteen-minute loop

The brief says: *walk the demo end to end first — paste the URL, confirm, open a
skill, expand a section, add one from the Library, set the destination, publish,
then look at the Agent Page and Website Preview. That fifteen-minute loop is the
product.* You can walk exactly that loop here, and every step hits a real
endpoint.

1. **Paste a URL.** `POST /api/ingest/scan` reads the site — the landing page and
   the product pages its navigation points at — and builds the catalog from the
   site's own URL structure, then ranks the industry from signal words and
   records which of the catalog our `site_phrases` already recognise. The catalog
   is everything they sell; the registry decides which rows arrive with a
   template, not which rows appear. Use `bluebonnetbank.com` to run against the
   bundled fixture instead of the network.
2. **Confirm the industry.** A suggestion with a confidence score, plus every
   other industry behind a disclosure. Confirming creates the vendor record.
3. **Confirm the matches.** Their words on the left, the registry product on the
   right, confidence in the middle. Tick what is right. Nothing was bound before
   this click; confirmed offerings become **draft** skills.
4. **Open a skill.** The editor reads top to bottom as the customer's journey:
   compiled entry prompt, sections, destination.
5. **Expand a section.** The actual script — what the AI says, what it asks in
   order, and one line saying where the conversation goes next.
6. **Add one from the Library.** Fully drafted on arrival. Cross-industry adds
   carry an origin badge.
7. **Reorder a section.** Watch the entry prompt regenerate. Try deleting a core
   one; it is refused.
8. **Set the destination.** Outputs and delivery mode.
8b. **Edit the wording.** Under the entry prompt: your text for how it opens,
   how it is paced and how it closes, interleaved with the parts that stay
   ours — the questions, which are edited as sections, and the guardrail.
9. **Publish.** Refused until there is somewhere to deliver. Then the start link
   goes live and the manifest regenerates.
10. **Agent page and website preview.** The manifest an AI reads, and the two
    lines of HTML that are the entire integration.
11. **Preview the customer run.** Executes the real MCP tools end to end.

There is a scripted version of this walk that runs in headless Chrome and asserts
on every step:

```bash
pip install websocket-client
uvicorn a2b.api.main:app --port 8011 &
python scripts/drive_console.py --port 8011 --out .shots
```

```
Console walkthrough passed:
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

---

## What is built, and what is not

Against the brief's suggested build order:

| # | Item | Status |
|---|---|---|
| 1 | Tenancy: `vendor` + `vendor_skill` with version pinning | Built |
| 2 | Skill editor, prompt compiler, publish → universal link | Built |
| 3 | Manifest generator wired to publish | Built |
| 4 | Section chaining in the MCP server | Built |
| 5 | Destination: outputs assembly and delivery routing | Built, with caveats below |
| 6 | Ingest matcher with suggest/confirm | Built |
| 7 | Library cross-industry sections, funnel events, terminal chooser | Built |
| — | Authentication and per-tenant authorization | Built |

### Who can do what

Accounts are people; vendors are workspaces; a `vendor_member` row joins the two
with a role. Three things about that are worth stating because they are not the
obvious choices.

**Membership is keyed by email rather than user id.** An invite is written before
the invitee has an account, and becomes live access the moment they sign up. The
alternative needs a reconciliation pass on every signup and gets it wrong when
somebody is invited twice.

**Reaching for another tenant's object returns 404, not 403.** A 403 confirms the
id is real, which is information the caller has not earned. Members of a vendor
who lack the *role* for an action do get a 403, because at that point they
already know the thing exists.

| | viewer | editor | admin |
|---|---|---|---|
| Read the workspace, skills and runs | yes | yes | yes |
| Edit sections, set the destination, publish | | yes | yes |
| Delete a skill, manage the team | | | yes |

Session tokens are stored as SHA-256 digests, so a copy of the database is not a
set of working logins. Passwords are PBKDF2-HMAC-SHA256 at 600k iterations, with
the iteration count stored per hash so it can be raised later without
invalidating anyone. `tests/test_auth.py` covers the isolation properties
directly: what it asserts is not that login works but that Bob cannot read,
rename, publish or delete anything of Alice's.

The public surface is untouched by all of this. A customer's AI has no account
here, so `/health`, `/v/{vendor}/…` and `/mcp` stay open — scoping there comes
from the session token in the tool arguments. There is a test for that too,
because closing those by accident would break the product silently.

### Staff, and the internal portal

`/internal` reads across every tenant: which companies exist, what they have
built, and whether anyone's customers are getting through an intake. It is the
only place in the system where that is possible, and the routes behind it
(`/api/admin/*`) are the only ones that ignore `vendor_member`.

Access comes from `A2B_STAFF_EMAILS` in `/etc/a2b/a2b.env`, comma-separated.
Three consequences worth knowing:

- **Empty means nobody.** An instance that forgets to configure it has no
  portal, rather than one open to every account on it. That is what the default
  is for, and there is a test that keeps it that way.
- **Not a rule about the email domain.** Signup never verifies an address, so
  "anyone at our domain" would let a stranger sign up as `someone@lenni.io` and
  read every bank's data. Editing the file is the grant.
- **Read per request.** Removing an address stops an existing session on its
  next call, with no migration and no row to remember to delete.

Non-staff get 404 from every internal route, signed in or not, and the portal's
sign-in page says the same thing to both. A 403 would confirm that the portal
exists and that getting onto a list is the only thing in the way.

The portal shows customers' actual answers, because support cannot be run from
counts alone. `staff_access` records who opened which company and which run,
and the log is readable inside the portal — an audit trail nobody can read is
decoration. It is kept out of `funnel_event` so a bank reading their own numbers
does not find our support visits mixed into them.

### Honest gaps

These are deliberate omissions, not oversights. A half-built version of each
would be worse than an obvious absence.

- **No TLS.** The deployed instance is plain HTTP behind nginx. The session
  cookie is `HttpOnly` and `SameSite=lax` but its `Secure` flag is off, because
  a `Secure` cookie is simply never sent over HTTP. Put a certificate in front,
  set `A2B_COOKIE_SECURE=1`, and that resolves itself. Until then, treat the
  deployment as a demo address rather than somewhere real credentials belong.
- **No password reset.** You can change a password while signed in; there is no
  emailed reset link, because there is no outbound email.
- **No outbound email.** Team invites are recorded in the database; nothing is
  sent. The email *destination* mode records an address and tells the customer's
  AI to use it, but this system does not send the package itself.
- **Convey is a link, not an integration.** Connecting Convey stores a URL and
  routes the customer to it. There is no API call into Convey, no upload, no
  delivery receipt.
- **`fdic-bank-data` and `id-verify` are registry entries only.** They appear in
  the app catalog marked unimplemented. `lenni_match_vendors` returns an explicit
  `not_implemented` error rather than a placebo result.
- **Ingest does not render JavaScript.** It reads the landing page and up to
  eleven pages its navigation points at, and the catalog comes from the links in
  that HTML, so a site built entirely client-side still scans as nearly empty.
  Nothing here executes a page. It does read the site's `robots.txt` first and
  obey it, which means a site that disallows us scans as nothing at all — the
  API answers 403 and says so, rather than reporting a bank with no products.
- **The catalog is proposed, never authoritative.** It is read off the site's URL
  layout, filtered by what a path says it is about, so a page the bank files
  under a product section but does not sell is a row someone has to untick.
- **Analytics are events, not dashboards.** `funnel_event` rows are written on
  every meaningful action; nothing aggregates them beyond a count per event.
- **The registry is a representative subset.** 21 products across 5 industries
  with templates, plus 13 more industries catalogued without templates. The brief
  describes 39 banking products and roughly 200 across 18 industries. Adding them
  is content work, not code work — see below.

---

## Architecture

```
                    ┌──────────────────────────────────────┐
   vendor  ───────► │  Vendor console        /console/     │
                    │  authoring surface                   │
                    └───────────────┬──────────────────────┘
                                    │  /api/*
                    ┌───────────────▼──────────────────────┐
                    │  FastAPI            a2b/api/main.py  │
                    └───┬────────────────────────┬─────────┘
                        │                        │
        ┌───────────────▼──────────┐   ┌─────────▼────────────────┐
        │  Content registry        │   │  Tenant state  (SQLite)  │
        │  read-only, on disk      │   │  vendor, vendor_skill,   │
        │  industries, products,   │   │  sections, profiles,     │
        │  templates, library,apps │   │  sessions, funnel        │
        └──────────────────────────┘   └──────────────────────────┘
                        │  seed: deep copy @ version T, pinned
                        └────────────────────────►

   two doors out, both regenerated on publish
        ├── /v/{vendor}/start/{skill}   universal link  ── humans
        └── /v/{vendor}/llms.txt|.md    agent manifest  ── AIs
                                    │
                    ┌───────────────▼──────────────────────┐
customer's AI ────► │  MCP connector    /mcp/call          │
                    │  section chaining, validate, deliver │
                    └──────────────────────────────────────┘
```

The split that matters is registry versus tenant state. Industries, products and
skill templates are **content on disk**, versioned with the code — which is what
lets a template carry a version number and a changelog that a vendor's pinned
copy can be compared against. Everything a vendor owns and edits is in SQLite.

---

## The data model

```
industry ──< product (registry row) ──< skill_template (versioned, changelog)
                                            │
                                            │ seed: copy at version T, pin
                                            ▼
vendor (tenant) ──< vendor_skill ───────────┘
                      ├─ skill_section[]  ordered; says / asks[] / collects[];
                      │                   core flag; origin_industry tag
                      ├─ apps[]           per section
                      ├─ entry_prompt     compiled, regenerated on every save
                      ├─ destination      outputs{pdf,md,doclist},
                      │                   mode: convey|link|email, target,
                      │                   final-prompt text overrides
                      └─ status           draft | published

customer_profile (email-keyed, NOT vendor-scoped)
      └──< session (skill @ version, section_status, facts, funnel events)
```

Three schema decisions worth calling out, because each encodes a rule:

**`seeded_from_template_version` is a stored string, not a foreign key.** A
foreign key would create a live relationship, and a live relationship is exactly
what pinning forbids. The pin is a historical fact about a copy that already
happened.

**`section_status` is keyed by section id, not by index.** A vendor can add,
remove or reorder sections while a customer is mid-run. Index-keyed status would
silently point at the wrong section after any of those.

**`customer_profile` has no `vendor_id`.** Profiles are portable across vendors
by design — it is what would make the second skill a customer runs faster than
the first.

That reuse is **switched off** and the table currently holds contact records
only. An email address is something other people know, and while a session
inherited a profile's facts on the strength of one, anyone who could reach the
open MCP endpoint could start a run as somebody else — putting that person's
private answers into a package addressed to a lender of the caller's choosing —
and could overwrite the profile on the way through. Sessions now start empty and
keep their facts to themselves. The reuse returns when a customer can prove the
address is theirs, which needs the outbound email in #15. See #24 and
`tests/test_profile_privacy.py`.

---

## Module map

| File | Responsibility |
|---|---|
| `a2b/config.py` | Paths, the public base URL, and the guardrail constant |
| `a2b/db.py` | SQLite schema and connection handling |
| `a2b/registry.py` | Loads industries, products, templates, library, apps; resolves a template into concrete sections |
| `a2b/skills.py` | Vendors, seeding with pinning, section CRUD, publishing, template adoption |
| `a2b/compiler.py` | Entry prompt and final prompt compilation; publish preconditions |
| `a2b/links.py` | Universal start link and terminal deep links, with honest support flags |
| `a2b/manifest.py` | `llms.txt`, `llms.md`, `/agents` data, webmaster packet |
| `a2b/sessions.py` | Profiles, sessions, section chaining, validation, funnel events |
| `a2b/destination.py` | Output assembly (PDF / markdown / doc list) and delivery routing |
| `a2b/ingest.py` | Site scanning, industry detection, product matching, confirmation |
| `a2b/catalog.py` | Everything a business sells, read off its own URL structure — no registry involved |
| `a2b/robots.py` | The site's own robots.txt, read before we read them |
| `a2b/analytics.py` | The only cross-tenant reads: portfolio rollups, drop-off, the staff access log |
| `a2b/mcp/server.py` | The tool surface |
| `a2b/api/main.py` | FastAPI routes: console API, public doors, MCP endpoint |
| `console/` | Vendor console — vanilla JS, no build step |
| `internal/` | Staff portal at `/internal` — same, deliberately unlike the console to look at |
| `content/` | The registry (see below) |
| `scripts/seed_demo.py` | Reproduces the demo's Bluebonnet scenario |
| `scripts/drive_console.py` | Headless-Chrome walkthrough with assertions |
| `scripts/drive_internal.py` | The same for the staff portal, including what a non-staff account sees |

---

## The seven locked rules

Full detail with code references in [`docs/MODEL_RULES.md`](docs/MODEL_RULES.md).
In brief:

1. **Template → your version.** Seeding copies and pins. Updates are
   vendor-initiated only.
2. **Suggest, confirm, seed.** Ingest proposes; a human confirms; nothing is
   auto-bound.
3. **Sections replace legs.** `section_status`, chained over the connector, invisible to
   the vendor.
4. **Two publish outputs, always.** One link for humans, the manifest for AIs.
5. **Secure delivery is the terminal step.** Convey is the default, not a
   requirement.
6. **The guardrail rides every session.** Introductory only — never a credit
   decision, never a rate quote.
7. **One link, every AI.** The universal link is the plan of record.

---

## The content registry

Everything in `content/` is Lenni-authored content, versioned with the code.
Adding to it requires no code changes.

| File | Contains |
|---|---|
| `industries.yaml` | 18 industries. 5 have templates; 13 are catalogued so ingest can still classify a vendor and record demand. |
| `products.yaml` | 21 products with `site_phrases` — the matcher's raw material. |
| `core_sections.yaml` | The core section set per industry. Banking is collateral → deal → borrower. |
| `templates/*.yaml` | 18 versioned skill templates with changelogs. |
| `library_sections.yaml` | 14 pre-drafted addable sections across 5 industries. |
| `apps.yaml` | 8 apps, each flagged implemented or planned. |

### Adding a product

```yaml
- key: uw-marine
  industry: banking
  name: Marine & Recreational
  tier: 2
  template: uw-marine          # omit if no template yet
  site_phrases:                 # written in VENDOR voice, not customer voice
    - boat loans
    - marine financing
    - rv and camper loans
```

`site_phrases` is the single most important field for ingest quality. Write the
words that appear on a vendor's own marketing pages. Do not write the words a
customer would type into a chat box — that is a different vocabulary aimed at
different text, and mixing them degrades both.

### Adding a template

```yaml
- id: uw-marine
  version: "1.0"
  name: Marine & Recreational
  product_key: uw-marine
  core: banking                # pulls collateral -> deal -> borrower
  overrides:
    Collateral:
      name: Vessel
      asks_append:
        - Length, year and engine hours?
  extra_sections:
    - name: Slip & storage
      summary: Where it lives when it is not in the water
      apps: []
      says: One thing lenders ask that owners forget — where does it live?
      asks:
        - Marina, dry stack, or trailer?
      collects: [storage arrangement]
  changelog:
    - version: "1.0"
      date: "2026-08-11"
      note: Initial release.
```

**Bump `version` and add a changelog entry for any change to section content.**
The version string is what gets pinned, so an unversioned edit silently changes
what new vendors seed while claiming to be the same template. Existing pinned
skills are unaffected either way — that is the point — but the pin becomes a lie
about which content was copied.

---

## The MCP tool surface

Tool names keep the `lenni_` prefix from the existing connector. The brief is
explicit that these are the same tools gaining tenancy, not replacements.

| Tool | Purpose |
|---|---|
| `lenni_list_services` | Published services for a vendor. Drafts never returned. |
| `lenni_start_session` | Begin a run. Returns the first section and the guardrail. |
| `lenni_get_section` | The section currently in progress. |
| `lenni_complete_section` | Mark done, receive the next. **This is the chaining step.** |
| `lenni_save_deal_facts` | Persist facts mid-section without advancing. |
| `lenni_validate_package` | Gaps and the next question. Never judges the deal. |
| `lenni_get_progress` | Section-by-section status. |
| `lenni_finish` | Assemble outputs, return the closing message and destination. |
| `lenni_match_vendors` | Marketplace only. Disabled inside a vendor session. |

```bash
curl -s localhost:8000/mcp/call -H 'content-type: application/json' -d '{
  "name": "lenni_start_session",
  "arguments": {"vendor":"bluebonnet-community-bank","skill":"multifamily-perm",
                "email":"borrower@example.com"}
}' | python -m json.tool
```

Errors come back as a structured `{"error": {"code", "message", "hint"}}` object
rather than an HTTP error, because the caller is a language model and a hint it
can act on is worth more than a status code.

### The marketplace switch

`ToolContext.vendor_slug` being set means the session is running on a vendor's
own machine, and vendor matching is off. On a bank's own site the customer works
with that bank's products — not a comparison against its competitors. The
no-vendor path is the generic marketplace.

---

## HTTP API

Interactive docs at `/docs`. The surface, grouped by audience:

**Registry (read-only)**
```
GET    /api/registry/industries
GET    /api/registry/products?industry=banking
GET    /api/registry/apps
GET    /api/registry/library
```

**Vendors**
```
GET    /api/vendors
POST   /api/vendors
GET    /api/vendors/{id}                     vendor + skills + members + funnel
POST   /api/vendors/{id}/members
GET    /api/vendors/{id}/sessions
```

**Ingest**
```
POST   /api/ingest/scan                      {url, vendor_id?, html?}
POST   /api/ingest/{run_id}/confirm          {vendor_id, match_ids[], industry_key?}
GET    /api/ingest/demand                    unmatched offerings = template roadmap (staff)
```

**Internal portal** — staff only, 404 to everyone else. See *Staff, and the
internal portal*.
```
GET    /api/admin/overview?days=30            every company, with totals
GET    /api/admin/companies/{vendor_id}       products, drop-off, team, runs
GET    /api/admin/runs/{session_id}           one customer's answers; logged
GET    /api/admin/access-log                  who among us opened what
```

Pass `html` to `scan` to skip the network — the console does this for the sample.

**Skills**
```
POST   /api/vendors/{id}/skills/seed?template_id=...
POST   /api/vendors/{id}/skills/custom?name=...
GET    /api/skills/{id}                      skill + links + final prompt + warnings
PATCH  /api/skills/{id}?name=...
DELETE /api/skills/{id}
POST   /api/skills/{id}/publish              409-equivalent if no destination
POST   /api/skills/{id}/unpublish
PUT    /api/skills/{id}/destination
PUT    /api/skills/{id}/prompt-texts          vendor wording; null puts ours back
POST   /api/skills/{id}/connect-convey?url=...
GET    /api/skills/{id}/template-diff
POST   /api/skills/{id}/adopt-template?keep_added=true
```

**Sections**
```
POST   /api/skills/{id}/sections/library     {industry_key, index}
POST   /api/skills/{id}/sections/blank?name=...
PUT    /api/sections/{id}                    {name?, says?, asks?, apps?}
DELETE /api/sections/{id}                    409 on a core section
POST   /api/sections/{id}/move?delta=-1
POST   /api/sections/{id}/apps?app_id=...
DELETE /api/sections/{id}/apps?app_id=...
```

**Public doors**
```
GET    /v/{vendor}/llms.txt
GET    /v/{vendor}/llms.md
GET    /v/{vendor}/agents
GET    /v/{vendor}/webmaster
GET    /v/{vendor}/start/{skill}             terminal chooser, 404 on a draft
```

In production these live on the vendor's own domain. `/v/{vendor}/…` is the
development shape; the vendor's webmaster maps `/llms.txt` and `/start/*` to
them, which is the two-line integration in the webmaster packet.

**MCP**
```
GET    /mcp/tools
POST   /mcp/call                             {name, arguments, vendor?, email?, session_id?}
```

---

## The console is a clone of the demo

`console/index.html` and `console/styles.css` are the demo's markup and CSS,
extracted verbatim. `console/app.js` keeps the demo's function names, DOM ids and
HTML strings, so the two render the same screens — but every array the demo held
in memory is now a real record, and every mutation is a request the server can
refuse. Put `a2b-demo.html` and `http://127.0.0.1:8000/console/` side by side and
they should be hard to tell apart until you look at the data.

`scripts/drive_console.py` walks the whole thing in headless Chrome and fails on
any JavaScript error, so a change that breaks the clone breaks the build.

### Where the behaviour underneath still differs

The demo is a click-through. A few of its behaviours are placeholders that would
be wrong to reproduce literally against real data. The screens look the same;
these are the things that happen differently when you press the button.

**Publishing a skill with no destination is refused.** The demo lets you publish
one with Convey unconnected. A published skill with nowhere to deliver strands a
real customer at the final step, after they have done all the work.

**The entry prompt carries the first section, and the address of the second.**
This is the most important thing on this page, and it has been wrong twice.

*The first version* named the sections and told the customer's AI to *connect to
the skill over WebMCP* at a URL. No consumer terminal can do that. Claude,
ChatGPT and Grok all require a connector to be registered in settings before its
tools exist in a chat; none will dial an MCP server named in the text of a
message. So the customer clicked a start link, their AI read an instruction it
could not follow, and — being helpful — it improvised a plausible intake of its
own. The vendor configured four sections and the customer answered someone
else's questions. That is worse than an error, because nothing looks broken.

*The second version* over-corrected and inlined the entire workflow. That fixed
the questions but broke three other things: the customer's opening message became
a 4,800-character wall of text; the AI could see the ending before it began, so it
batched questions and raced ahead instead of holding a conversation; and the
vendor's complete question set became readable by anyone holding a start link,
which for a bank's underwriting criteria is not ours to publish.

*Now* `compile_entry_prompt` carries section 1 verbatim and the URL of section 2.
Each section is served at `/v/{vendor}/start/{skill}/{n}.md` and names its
successor, so the intake unfolds as the customer works through it. The last page
carries the compiled final prompt, so the run has a real ending. Editing a
question in the console changes what the next customer's AI asks, because
`recompile` runs on every save and `recompile_all` runs at every startup — and a
*reorder* is now the sharp case, because it changes which section is handed over
at the click.

Two things this is still not. It is instruction-following, not enforcement: an AI
could fetch every page up front, and nothing here validates an answer or saves
progress. And the whole workflow remains readable at
`/v/{vendor}/start/{skill}.md` for anyone who wants it — the chain shortens the
opening message and stops casual exposure, it does not make the intake private.

Both are fixed by the same thing: a **remote MCP connector**, where the server
hands out one section at a time and holds the state. The tools for that already
exist and are exercised by the console's customer-run preview and by
`test_customer_runs_the_skill_over_mcp`; what is missing is the transport. See
issue #28. Everything on this page is the stopgap that gets the shape right in
the meantime, and `compile_full_entry_prompt` stays as the floor beneath it for
assistants that cannot open a link at all.

**Start links open the terminal; they do not copy a URL.** Rows with a working
deep link are real `<a>` elements with real `href`s, so they open on click, on
middle-click and on cmd-click, and the browser shows the destination on hover.
Every row also carries a separate **copy** button, because a vendor needs two
different things from this list — to try the link, and to paste it into their
own site.

Where the four terminals actually stand:

| Terminal | Behaviour |
|---|---|
| **ChatGPT** | `chatgpt.com/?q=` — opens and sends. |
| **Claude** | `claude.ai/new?q=` — prefills and waits. Anthropic removed auto-submit in late 2025; prefill still works. Claude Desktop also has an official `claude://` scheme. The row says "fills the box, you press send" rather than implying it starts on its own. |
| **Grok** | `grok.com/?q=` — opens and sends, **if the customer is already signed in**. A signed-out visit loses the prompt through the login redirect, and grok.com sometimes interposes a Cloudflare check. Reported as "the prompt never appears"; the note on the button now says so. |
| **Copilot** | **No link.** Microsoft removed `?q=` prefill after the "Reprompt" attack, which used that exact parameter to inject prompts and exfiltrate conversation data; patched January 2026. The URL still loads Copilot and renders the text as a page heading, leaving the composer empty — so a link here looks like it worked and did not. This row copies the prompt instead. |

`supported` means the URL is real. Nothing aspirational ships as if it works, and
`test_links.py` fails the build if `supported` and `url` ever disagree.

One guard worth knowing: a `?q=` link is a URL and URLs have limits. Carrying one
section keeps a three-section intake around 1,900 characters encoded, well inside
what every current browser and all three receiving apps accept, and it no longer
grows as the vendor adds sections. Past `links.MAX_QUERY` (6,000) — which now
takes a single pathological section — the link switches to a compact prompt that
names the sections and sends the AI to the markdown workflow. Truncating instead
would cut the back half, which is exactly where the guardrail and the delivery
instruction live. The copy button always gives the full text regardless.

**Draft skills do not get live start links.** A draft has no public start page, so
every link it produces 404s. The console still renders the panel — previewing what
will ship is the point of it — but `links.live` is false and the panel says so. One
of these went out to a customer's Claude and dead-ended.

**"Trust Services" matches a registry row.** In the demo it is a hard "No match".
Here it is a tier-3 product with `site_phrases` and no template, so ingest
recognises what it is and routes it to a custom skill. Knowing what something is
while lacking a template for it is better information than not knowing at all,
and it is a cleaner roadmap signal.

**The matcher discards generic-word-only matches.** "Aircraft financing" and
"Equipment Finance" share only the word `financing`, and that is not a match.
Without this rule every product in an industry is a weak match for every offering
in it, because they all say "loans" or "financing", and the vendor is handed a
confirmation screen full of plausible-looking nonsense.

**"Sarah Whitley" has to log in.** The demo ships with an admin already signed
in, because it has no accounts. Here the sidebar names whoever actually holds
the session, and "Log in" on the homepage is a real form. Scanning a site is
still anonymous — that is the first thing a prospect does and asking for a
password beforehand would kill the funnel — but *keeping* the result creates the
account. The scan is held across the signup and resumed, so the interruption
costs the user nothing.

**`bluebonnetbank.com` resolves to a bundled fixture.** The domain is not real, so
`content/demo_sites/` holds the page the demo describes and `ingest.fetch` checks
there before the network. The parser, the matcher and the confirmation screen all
run for real — only the transport is short-circuited. Any real URL is fetched
normally.

### On the matcher being lexical rather than model-based

Matching a page heading to a registry of known products is a bounded string
problem: the candidate set is small, the vocabulary is domain-specific, and
`site_phrases` already encodes the phrasings that occur in the wild. A lexical
matcher is free, instant, deterministic, and — most importantly — explainable. It
can show the vendor which phrase it matched, which is what makes the confirm step
meaningful rather than a rubber stamp on a black box.

If measurement later shows this failing on real sites, add a model. Measure it
against this baseline, not against nothing.

---

## How the existing Lenni build maps in

From the brief's mapping table, and what happened to each piece:

| Existing | Here |
|---|---|
| `lenni_start_session` | Gains `vendor` and `skill`. No-vendor path is the marketplace. |
| `lenni_find_loan_package` / 27-product catalog | Reconciled onto `content/products.yaml`. The registry wins on taxonomy. |
| `lenni_get_underwriting_package` | Became "load skill section content", served per vendor skill at its pinned version. |
| `lenni_validate_package` | Kept as-is in spirit — it is the doc-checklist app's engine. |
| `lenni_save_deal_facts` | Kept. This is the session layer, now with `section_status`. |
| `lenni_get_user_context` | **Dropped** (#24). Loading a profile by email alone let a stranger read one, and told them which addresses had one. Returns with proof of the address — #15. |
| `lenni_match_banks` (FFIEC) | Becomes the `fdic-bank-data` app and the marketplace path. **Not ported** — the tool returns `not_implemented` rather than a placebo. |
| Workspace accounts, resume on any device | The customer portal, unchanged. Vendors get this separate console. |

The FFIEC pipeline, the borrower workspace, the underwriting maths and the PDF
generator live in the
[FFIEC-Data-pull](https://github.com/ALL-Dunkin/FFIEC-Data-pull) repository. This
repository is the vendor side only.

---

## Testing

```bash
. .venv/bin/activate
python -m pytest tests/ -q
```

```
52 passed
```

| File | Covers |
|---|---|
| `test_pinning.py` | Rule 1. Deep-copy semantics, two vendors staying independent, a shipped template version not moving a seeded skill, explicit adoption, refusal on custom skills. |
| `test_compiler.py` | Entry prompt content and regeneration on reorder; the produced sentence following the output toggles; editable voice over locked wiring; publish preconditions. |
| `test_chaining.py` | Draft skills unrunnable; the section walk; status surviving mid-run edits; core deletion refused; cross-industry origin tags; gap reporting; profile portability. |
| `test_manifest.py` | Drafts omitted from both forms; publish and unpublish round-tripping; the guardrail present; unsupported terminals reported honestly. |
| `test_matcher.py` | Offering extraction; industry detection; a weak tagline not stealing a product; quotes not borrowing the next service's copy; nothing bound before confirmation. |
| `test_api.py` | The full journey over HTTP, then a customer running the skill over MCP end to end. |

`test_pinning.py` is the largest file on purpose. A regression there rewrites
words a vendor has approved for talking to their own customers, silently, with no
user action to point at afterwards.

The console has its own assertion-driven walkthrough in
`scripts/drive_console.py`, which fails on any uncaught JavaScript error.

---

## Deployment notes

### The deployed instance

An EC2 box in `us-east-2` runs this behind nginx. Three commands cover the whole
lifecycle:

```bash
ssh a2b 'bash -s' < deploy/bootstrap.sh   # once: packages, venv, /var/lib/a2b
./deploy/deploy.sh                        # every time: rsync, pip, restart
./deploy/deploy.sh --seed                 # ... and reseed the demo vendor
ssh a2b 'sudo journalctl -u a2b-api -f'   # logs
```

### The public link

Nothing is open in the instance's security group. The public address comes from a
Cloudflare **quick tunnel**, run by `a2b-tunnel.service`:

```bash
ssh a2b 'cat /var/lib/a2b/public-url.txt'   # the current address
ssh a2b 'sudo systemctl restart a2b-tunnel' # new address, see the warning below
```

This was chosen over opening port 80 for one reason: the console has a login, and
a tunnel gets TLS terminated at Cloudflare for free. Port 80 would put passwords
on the wire in cleartext. It also means `A2B_COOKIE_SECURE=1` is on, which is
correct — and the SSH-tunnel route on `127.0.0.1:8020` keeps working anyway,
because browsers treat localhost as a secure origin.

**A quick tunnel's hostname changes every time it reconnects.** That hostname is
compiled into published entry prompts, so `deploy/tunnel.sh` rewrites
`A2B_MCP_BASE` and restarts the API whenever it changes, and `recompile_all` at
startup rebuilds every stored prompt around the new address. That keeps the
system consistent, but it does not un-send prompts a customer already pasted into
their AI: links handed out before a restart go dead. Do not give this address to anyone
outside a demo. The fix is a named tunnel on a domain you control, which pins the
hostname permanently — the only reason it is not set up is that it needs a
Cloudflare account and a domain.

`deploy.sh` deliberately does not restart the tunnel, so shipping code does not
change the address.

### The layout the scripts assume

| Path | What |
|------|------|
| `/opt/a2b` | the deploy tree, replaced wholesale by every rsync |
| `/var/lib/a2b` | the database — deliberately outside the tree, so `--delete` cannot reach it |
| `/etc/a2b/a2b.env` | per-instance settings, written once and never synced |
| `a2b-api.service` | uvicorn on `127.0.0.1:8000`, two workers, restart on failure |

Nothing listens publicly except nginx on 80. If the console does not load from
outside, the security group is the first thing to check — the app being healthy
on the box tells you nothing about whether AWS is letting anyone in.

### Before it is more than a demo

- **Authentication is in place; TLS is not.** Set `A2B_COOKIE_SECURE=1` in
  `/etc/a2b/a2b.env` the moment there is a certificate, and not before — the
  cookie would stop being sent over plain HTTP and nobody could log in.
- **`A2B_MCP_BASE` must be the externally reachable host, scheme included.**
  `bootstrap.sh` reads it from instance metadata on first run. Re-point it at the
  real domain when there is one. It is compiled into every entry prompt, and
  those prompts get pasted into customers' AI sessions, so a `localhost` value
  there produces links that fail in the customer's hands. It used to be stored as
  a bare hostname, which an agent reads as prose rather than as somewhere to go;
  `config._origin` now prepends `https://` if the scheme is missing, and
  `A2B_PUBLIC_BASE` can override the origin for the public pages separately.
- **SQLite is a skeleton choice.** It is fine for a single process. Real tenancy
  wants Postgres; the schema in `a2b/db.py` ports directly.
- **The public doors belong on the vendor's domain.** `/v/{vendor}/…` is the
  development shape. Production is `bluebonnetbank.com/llms.txt` and
  `bluebonnetbank.com/start/multifamily-perm`, proxied through.
- **ReportLab is optional.** Without it, `assemble()` produces markdown and the
  document list and notes that the PDF was skipped, rather than failing the
  customer's delivery at the last step.

---

## Glossary

Use the words in [`docs/GLOSSARY.md`](docs/GLOSSARY.md) exactly. Vendor, not
tenant. Industry, not category or vertical. Section, not leg. Skill template
versus skill. When code, tickets or conversation disagree about a word, the
glossary wins — and the canonical copy lives in the OS
(`packages/system-nomenclature.md`), not here.

---

## Source documents

This repository implements:

- `DEV-BRIEF-a2b-system.md` — Doak, 2026-07-31. The specification.
- `a2b-demo.html` — the click-through prototype.

Neither is committed here; both are the manager's documents. Business truth lives
in the OS (`lenni/skill-library/`, `tables/`, `packages/system-nomenclature.md`).
Code and tickets stay in GitHub; requirements flow from the OS.
