# Lenni A2B: project overview

A2B stands for **Agent to Business**. It answers one question for a service
business: *how does your customer get from their own AI to you?*

This document covers the vision, the architecture of this repository, how the
pieces are deployed, and where to see them running.

## Live sites

| Site | URL | What it is |
|---|---|---|
| Lenni | <https://lenni.io> | Public Lenni website |
| A2B production | <https://app.lenni.io> | The Lenni workspace that banks sign in to |
| A2B staging | <https://a2b-staging.lenni.io> | Pre-release copy of the workspace (access-controlled) |
| A2B sandbox | <https://a2b-sandbox.lenni.io> | Demonstration and experiment copy (access-controlled) |
| ProofLink (Plaid) | <https://a2b-sandbox.lenni.io/plaid-demo/> | Identity verification demo on Plaid (access-controlled) |
| ProofLink Basic (Didit) | <https://a2b-sandbox.lenni.io/prooflink-basic/> | Identity verification demo on Didit |
| ProofLink Persona | <https://a2b-sandbox.lenni.io/prooflink-persona/> | Identity verification demo on Persona |

Staging and sandbox ask for a login before anything loads. Their credentials
are not part of this repository.

## Vision

A bank, a lender, or any other service business, which A2B calls a
**vendor**, already describes what it sells on its own website. Its customers
increasingly start in an AI assistant: Claude, ChatGPT, Copilot, Grok. Today
the assistant improvises. It guesses which questions matter, invents product
facts, and stops at the point where a real application would begin.

A2B lets the vendor take control of that conversation without building an AI
product of its own:

1. **Read the vendor's site.** A2B scans the website, proposes an industry and a
   catalog of offerings, and matches each one to a template from a curated
   registry. A person confirms every match. Nothing is bound automatically.
2. **Turn each offering into a skill.** A skill is the vendor's own intake
   script, split into ordered **sections**. Each section has what the AI says,
   what it asks, and what it collects. The vendor edits the wording; the
   questions, guardrails and delivery stay governed.
3. **Publish two doors.** Publishing produces a **universal start link** for
   people and an **agent manifest** (`llms.txt` / `llms.md`) for AIs. Two lines
   of HTML put both on the vendor's website.
4. **Let the customer's own AI run it.** The customer stays in their own
   assistant, on their own tokens. The assistant connects to A2B over MCP and is
   walked through the vendor's sections one at a time.
5. **Deliver securely.** The finished package (PDF, markdown, document list) is
   routed back to the vendor through the destination it chose.

Rules that hold throughout:

- Product facts come from the registry or the vendor's confirmed data, never from
  the model.
- Every session carries the guardrail: introductory only. A2B never makes a
  credit decision and never quotes a rate.
- One link works in every AI. Where an assistant cannot accept a prefilled
  prompt, the link says so rather than failing silently.
- Editing a skill changes the next session without anyone replacing links that
  are already on the vendor's site.

### ProofLink

**ProofLink** is the identity-verification product inside the Lenni workspace.
A bank administrator gives each employee a permanent customer link. The customer
gives consent on Lenni's page, completes identity verification on the vendor's
hosted page, and the employee receives an outcome email with a sign-in link to
the result. No ID image, selfie, or ID number is stored or emailed. The
verification vendor is replaceable: the demos above run the same flow on Plaid,
Didit, and Persona. Version 1 verifies identity only; it makes no credit,
housing, employment, insurance, or watchlist decision.

## Architecture

```
                    ┌──────────────────────────────────────┐
   vendor  ───────► │  Vendor console        /console/     │
                    │  authoring surface                   │
                    └───────────────┬──────────────────────┘
                                    │  /api/*
                    ┌───────────────▼──────────────────────┐
   Lenni staff ───► │  FastAPI            a2b/api/main.py  │
   /internal        └───┬────────────────────────┬─────────┘
                        │                        │
        ┌───────────────▼──────────┐   ┌─────────▼────────────────┐
        │  Content registry        │   │  Tenant state  (SQLite)  │
        │  read-only, on disk      │   │  vendors, skills,        │
        │  industries, products,   │   │  sections, members,      │
        │  templates, library,apps │   │  sessions, funnel events │
        └──────────────────────────┘   └──────────────────────────┘
                        │  seed: deep copy at version T, pinned
                        └────────────────────────►

   two doors out, both regenerated on publish
        ├── /v/{vendor}/start/{skill}   universal link  ── people
        └── /v/{vendor}/llms.txt|.md    agent manifest  ── AIs
                                    │
                    ┌───────────────▼──────────────────────┐
customer's AI ────► │  MCP connector    /mcp               │
                    │  section chaining, validate, deliver │
                    └──────────────────────────────────────┘
```

The split that matters is **registry versus tenant state**. Industries,
products and skill templates are content on disk, versioned with the code. That
is what lets a template carry a version number and a changelog. Everything a
vendor owns and edits lives in the database.

### Components

| Area | Where | Responsibility |
|---|---|---|
| API | `a2b/api/main.py` | Console API, public doors, MCP endpoint, staff routes |
| Registry | `a2b/registry.py`, `content/` | Industries, products, versioned skill templates, library sections, apps |
| Skills | `a2b/skills.py` | Seeding with version pinning, section editing, publishing, template adoption |
| Prompt compiler | `a2b/compiler.py` | Entry and final prompts, rebuilt on every save; publish preconditions |
| Links | `a2b/links.py` | Universal start link and per-assistant deep links with honest support flags |
| Manifest | `a2b/manifest.py` | `llms.txt`, `llms.md`, `/agents`, webmaster packet |
| Ingest | `a2b/ingest.py`, `a2b/catalog.py`, `a2b/robots.py` | Site scan, industry detection, product matching; obeys the site's `robots.txt` |
| Sessions | `a2b/sessions.py` | Customer sessions, section chaining, validation, funnel events |
| Destination | `a2b/destination.py` | Output assembly and delivery routing (Convey, link, email) |
| MCP | `a2b/mcp/` | Tool surface and transport for customers' assistants |
| Auth | `a2b/auth.py`, `a2b/authpages.py` | Accounts, sessions, per-vendor roles |
| Analytics | `a2b/analytics.py` | Portfolio rollups, drop-off, and the staff access log |
| Vendor console | `console/` | Authoring UI, plain JavaScript, no build step |
| Staff portal | `internal/` | Cross-tenant support view at `/internal` |
| ProofLink (Plaid) | `plaid_identity_verification/` | Standalone identity-verification service |
| Deployment | `deploy/` | Service units, nginx, tunnel, and per-environment overlays |
| Tests | `tests/` | Pinning, compiler, chaining, manifest, matcher, auth isolation, API journey |

### Data model

```
industry ──< product ──< skill_template (versioned, changelog)
                              │ seed: copy at version T, pin
                              ▼
vendor ──< vendor_skill ──< skill_section (ordered; says / asks / collects; core flag)
   │            ├─ entry_prompt   compiled, regenerated on every save
   │            ├─ destination    outputs + delivery mode + target
   │            └─ status         draft | published
   └──< vendor_member (email-keyed role: viewer | editor | admin)

customer_profile ──< session (skill @ version, section_status by section id, facts)
```

Three decisions encode rules:

- The template version a skill was seeded from is stored as a string, not a
  foreign key. A pinned copy must not follow later template edits.
- Section progress is keyed by section id, not position, so a vendor can reorder
  sections while a customer is mid-run.
- Reaching for another vendor's object returns 404, not 403, so ids are not
  confirmed to people who should not see them.

### Security posture

- Passwords use PBKDF2-HMAC-SHA256 at 600,000 iterations. Session tokens are
  stored as SHA-256 digests.
- Staff access is an explicit allow-list. An empty list means nobody, and every
  staff view of a vendor's data is logged.
- The public surface (start links, manifests, MCP) stays open by design, because
  a customer's AI has no account. Scoping there comes from the session.
- Customer profiles are not reused across sessions until a customer can prove
  the email address is theirs.

## Environments

| Environment | Purpose | How it changes |
|---|---|---|
| Production | What banks use | Promoted deliberately after review |
| Staging | Pre-release check | Follows the sandbox |
| Sandbox | Demos and experiments | Updated by hand |

All three run behind nginx on one AWS instance, reached through a named
Cloudflare tunnel so the hostnames stay fixed. Staging and sandbox add a login
in front of everything. The ProofLink demos each run as their own small service
with their own database and settings, so changing one cannot affect another.

## Running it locally

Python 3.9 or newer. No database server or API keys are needed.

```bash
git clone https://github.com/adimantamu/A2B-system.git
cd A2B-system
./run.sh
```

| | |
|---|---|
| Vendor console | <http://127.0.0.1:8000/console/> |
| API documentation | <http://127.0.0.1:8000/docs> |
| MCP tool surface | <http://127.0.0.1:8000/mcp/tools> |
| Health | <http://127.0.0.1:8000/health> |

Tests:

```bash
. .venv/bin/activate
python -m pytest tests/ -q
```

`README.md` has the full walkthrough, the HTTP API, the MCP tools, and the
content-registry format.

## Known gaps

- SQLite is a single-process choice; real multi-tenant use wants Postgres.
- Outbound email for invites and password reset is limited.
- Convey delivery is a link, not an API integration.
- Ingest reads HTML and does not run JavaScript, so client-rendered sites scan
  as nearly empty.
- The registry is a representative subset: 21 products with templates across 5
  industries, plus 13 more industries catalogued for classification.
- ProofLink in production depends on vendor contracts, resale terms, and privacy
  and FCRA review.
