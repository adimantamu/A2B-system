# How the bank's questions reach the customer's AI — and why the connector is the answer

**Status:** written 16 August 2026, after a full evening of live testing against the
running system. Every claim below is drawn from our own server logs rather than
from how the runs appeared in the chat window.

---

## 1. What the product does

A bank publishes a loan product. We generate a page for that product containing
every question the bank wants asked, in the bank's own wording and order. On the
bank's website, a button says **Start with Claude** or **Start in ChatGPT**. The
customer clicks, lands in their own AI, and that AI walks them through the bank's
intake and produces a finished package.

The customer installs nothing. The bank changes their questions in our console,
not on their website. That is the whole pitch, and it is sound.

The one assumption underneath it is that the customer's AI will go and read the
page we point it at. That assumption is what this document is about.

## 2. What we found

We ran the full four-section commercial intake repeatedly across ChatGPT and
Claude, and watched the server rather than the chat. Three distinct problems came
out, in increasing order of importance.

### 2.1 Terminals fetch differently, and change without notice

| Terminal | What our server actually saw |
|---|---|
| ChatGPT | Fetched the live page every time — 4:59pm, 5:43pm, 8:31pm, 9:21pm |
| Claude | Fetched directly at 4:30pm. At 9:27pm asked for `/robots.txt`, got a 404, and stopped fetching. Every turn after that read a cached copy found by web search |
| Microsoft's crawler | Asked for `/robots.txt` twice, 7:46pm and 8:47pm, both 404 |
| Copilot | Has not accepted a pre-filled prompt at all since January 2026 |

The consequence for Claude was concrete and would have been embarrassing in front
of a customer. It recited a confirmation code we had deleted from the page hours
earlier, because it was reading a crawled snapshot rather than the live document.
It then reported that one of the bank's sections "won't load" — while our server
was serving that exact page correctly. The page was fine; it simply wasn't in the
search index the assistant was reading from.

None of this is misbehaviour by Anthropic or Microsoft. It is ordinary variation
in how AI vendors retrieve web content, and it changes on their schedule, not ours.

### 2.2 A single read decays over a long conversation

Even when the page is fetched correctly, the assistant reads it once at the start
and then answers from memory. Four sections and forty minutes later it is working
from its own compressed summary. In one run it renamed two of the bank's sections
— "Business financials" came back as "Financials & Cash Flow" — and skipped the
closing steps entirely.

Nothing had been edited. This is decay, not staleness: it happens to a document
that never changes. Instruction-following degrades over a long conversation, and
no amount of rewriting the instructions removes that.

### 2.3 We could not see any of this until tonight

The most uncomfortable finding. Until this evening we had no server-side record of
whether a customer's AI had actually read a bank's page. Our only check was to ask
the assistant to quote a code from the document — which asks the customer to police
the assistant, puts our internal plumbing into their chat, and takes the word of the
one party with a reason to be wrong.

We now log every read. It paid for itself within the hour: it is how we established
that Claude was reading a snapshot, and it is the only reason we know the difference
between a run that worked and a run that looked like it worked.

## 3. What we fixed tonight

All shipped, tested and deployed.

- **`robots.txt`.** We were publishing pages for AI assistants to read and had never
  said so in the place a fetcher checks first. A missing file is ambiguous, not
  permissive, and Anthropic's fetcher stopped at that ambiguity.
- **No caching of the agent documents.** They are generated per request; without
  cache headers an intermediary can serve a stale copy, which breaks the core
  promise that a bank's edit reaches the next customer.
- **Server-side read logging**, described above.
- **A defined ending.** The intake now offers a summary, takes corrections a section
  at a time, and produces a PDF on confirmation. Previously it trailed off and the
  customer was left holding a chat transcript.
- **A checkpoint between every section**, so the assistant advances when the
  customer says so rather than when it decides the answers looked complete.
- **A re-read instruction before each section**, which pulls the bank's exact
  wording back to the front of the assistant's attention and makes decay visible in
  our logs.

These are real improvements. They make the good path better and the failure path
noisier. They do not make the mechanism dependable, and it would be a mistake to
present them as though they had.

## 4. What cannot be fixed this way

Everything in section 3 is an instruction written on a page, and every instruction
depends on an assistant we do not control choosing to read it and choosing to
follow it. We can improve the odds. We cannot make them certain, and we cannot stop
a vendor changing its retrieval behaviour next month.

Two structural limits follow, and both matter to a bank:

1. **We cannot guarantee the bank's questions were asked as written.** We can show
   the page was fetched. We cannot show what the assistant did with it.
2. **We cannot guarantee the bank's edit reached the customer.** It reaches the
   page immediately. Whether it reaches the conversation depends on the terminal.

A bank's compliance and model-risk teams will ask both questions in the first
meeting. "Usually" is not an answer they accept.

## 5. Three ways to deliver, and what each one buys

| | How the questions arrive | Works where | What it guarantees |
|---|---|---|---|
| **Link** | The AI opens our live URL | ChatGPT reliably; others variably | Current content at fetch time |
| **Inline** | The whole workflow travels inside the button's prompt | Anywhere with a text box | Current at click; no fetch to fail |
| **Connector** | Our server hands over one section at a time | Claude, ChatGPT, any MCP client | Current wording at every step, recorded server-side |

The inline option deserves a note, because it is the cheap fix available immediately.
A full four-section intake is 3,691 characters — around 5,500 once encoded into a
link. That fits comfortably in what these terminals accept, so the button can simply
carry the bank's workflow instead of pointing at it. Generated at the moment of the
click, it is exactly as current as a fetch would have been, and it cannot be defeated
by a crawler, a cache, a search index or a missing `robots.txt`. It works in Grok and
Copilot too.

That removes the reliability problem. It does not remove the auditability problem.

## 6. Why the connector is the right answer

The connector inverts the relationship. Instead of publishing a document and hoping
an assistant reads it correctly, our server holds the intake and hands out one
section at a time in response to a tool call. The assistant asks; we answer; we
record.

**It removes the retrieval problem entirely.** There is no page to crawl, no
snapshot to go stale, no search index to be missing, and no `robots.txt` to
misinterpret. Every terminal that speaks the protocol behaves identically, which is
the property we have spent this evening discovering we do not have.

**It removes the decay problem.** The current section's exact wording arrives one
turn before it is asked, every time, rather than being recalled from something read
forty minutes ago.

**It makes the intake auditable.** We record which sections were served, when, and
what was captured against each one. A bank can be shown their own questions being
delivered, rather than assured that they were. This is the difference between a
demo and a system a regulated institution can adopt, and it is worth saying plainly
that no amount of work on the document path produces it.

**It validates rather than narrates.** The server checks the captured answers
against the fields the bank requires and reports what is missing. Today the
assistant decides what is missing, and it decides well, but it is deciding.

**It assembles and delivers the package itself.** The finished document is produced
server-side and routed to the bank's secure channel, instead of the assistant
producing a file and the customer being told where to upload it.

**It keeps state.** A customer who closes the tab can resume. Today the intake lives
in a chat window and ends with it.

### The honest cost

The connector is not free to adopt. The customer, or their IT administrator, has to
add it once — and on an enterprise Claude account that means an administrator, not
the customer. That friction is exactly what the document path avoids, which is why
the answer is not "connector only".

The two are complements, and the shape is straightforward:

- **The document path is the acquisition channel.** Zero friction, works on a
  stranger's laptop, gets a customer from a bank's product page into a real
  conversation in one click. Best-effort by nature, and that is acceptable for
  preparation.
- **The connector is the delivery and compliance channel.** It is what we offer a
  bank that needs to prove their questions were asked, their answers were captured,
  and their package arrived. It is the tier worth paying for.

## 7. Recommendation

1. **Immediately:** make the button carry the workflow inline for terminals that do
   not fetch dependably. This removes tonight's failure class for every terminal at
   low cost, and needs no cooperation from any AI vendor.
2. **Next:** build the connector properly — the transport, the authentication, and
   a setup path a bank's IT department can follow without us on the call.
3. **Ongoing:** keep the read logging and report per-terminal behaviour with dates.
   It changes, we now measure it, and being the party that noticed first is worth
   more in a bank meeting than a demo that happened to go well.

## 8. What to say to a bank

> The intake works today with no installation — your customer clicks a button on
> your product page and their own AI takes them through your questions. How each AI
> vendor collects those questions varies and is outside anyone's control, so we
> don't rely on it: the button carries your workflow with it, generated fresh at
> click time.
>
> When you need to prove your questions were asked as written and your answers were
> captured, that is the connector. Your intake is served from our system one section
> at a time and every step is recorded against your account. That is the version
> your model-risk team should look at.

Do not claim the document path is enforced. It is instruction-following, and a bank
will establish that faster than we can talk past it. The connector is what makes the
claim true, and saying so plainly is what makes the connector worth buying.
