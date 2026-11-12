# Glossary

Use these words exactly. When code, tickets or conversation disagree about a
word, the glossary wins.

> **Where the authority actually lives.** The canonical glossary is
> `packages/system-nomenclature.md` in the OS, not this file. This copy exists so
> the repository is readable on its own. If the two diverge, the OS is right and
> this file is stale — fix it here rather than arguing from it.

| Term | Means | Not |
|---|---|---|
| **A2B** | Agent to Business — the category. "A2B runs on MCP." | "A2B runs on WebMCP" — that is a different, in-browser standard we do not use. |
| **Connector** | Our MCP server registered in the customer's own Claude or ChatGPT. The workflow arrives as tool calls. The user adds it; a prompt cannot. | "WebMCP", which registers tools inside a web page for a browser-resident agent. |
| **Staged prompt** | The fallback route for a customer with no connector: the start link carries section 1 and the address of section 2. | The product. It is instruction-following, not enforcement. |
| **Vendor** | The paying customer: a bank, a law firm, an agency. | "Tenant" — that is the technical isolation unit only. |
| **Industry** | The vendor's business type. Organizes the template library. | "Category", "vertical" |
| **Product** | A known service offering; one row in the registry, e.g. `uw-multifamily-perm`. | |
| **Skill template** | A Lenni-authored guided flow for one product. Versioned. Vendors never edit it. | |
| **Skill** (vendor skill) | The vendor's own published version, seeded from a template and then entirely theirs. | |
| **Section** | An ordered chunk of a skill. Banking core is collateral → deal → borrower, always in that order. | "Leg" — that word is dead. |
| **App** | A plug-in capability inside a section: loan-calculator, doc-checklist, id-verify, artifact, secure-delivery. | |
| **Entry prompt** | The compiled text a start link carries into the customer's AI. Generated from skill config. | Something a vendor writes by hand. |
| **Destination** | The skill's exit: what gets produced (PDF / MD / doc list) and where it is delivered. | |
| **Agent manifest** | The auto-generated `llms.txt` / `llms.md` and `/agents` page. Regenerates on every publish. | |
| **Session** | One run of a skill, chained section to section. | |
| **Profile** | A customer's accumulated facts, keyed by email and portable across vendors. | Something scoped to one vendor. |

## Words that carry a rule inside them

**Seeded.** A skill is *seeded from* a template, not *linked to* one. The word is
chosen to make clear that a copy was taken and the connection is historical.

**Pinned.** A seeded skill is pinned to the template version it copied. Pinning
is not caching — there is no refresh, and no upstream change reaches it.

**Confirm.** Ingest produces suggestions. A vendor *confirms* them. Nothing in
the system says "auto-detected" or "automatically added", because nothing is.

**Publish.** The moment a vendor consents to their skill being reachable. Before
it, the skill does not exist as far as customers and agents are concerned.
