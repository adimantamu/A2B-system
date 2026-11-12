# Rehearsing the demo

The demo now runs the doc-handoff path: the customer clicks a button on the bank's
product page, lands in their own assistant, and that assistant reads the bank's
published guide and runs the intake from it.

That path has exactly one failure worth rehearsing, and it does not look like a
failure. An assistant that cannot fetch the guide — browsing switched off, a
network that blocks us, a terminal that quietly drops links — will not say so. It
will produce a confident, plausible, generic loan intake, and nobody in the room
can tell it apart from the bank's own questions.

So we check, every time, before anyone else does.

## The check

The server records a `guide_read` event every time a `context.md` is fetched, so
the question is answered by our logs rather than by the assistant's own account of
itself. Check the vendor's funnel before and after the run: no `guide_read`, no
fetch, whatever the chat claims.

We used to have the assistant quote a code from the page instead. It worked, but
it asked the customer to police the assistant, and the code kept turning up where
it did not belong — in the opening reply, then signing the finished package. The
server saw the request; that is the party worth asking.

The second check is the questions themselves. Open the guide, read section 1's
first question, and see whether that is what came back — a generic intake reads
smoothly and is obviously not theirs once you have the real wording in front of
you.

| Outcome | What it means | What to do |
|---|---|---|
| `guide_read` logged, and section 1's real first question | It read the guide | Carry on |
| No `guide_read`, sensible-sounding questions | It never fetched, and is improvising | Stop. Use the paste fallback |
| Says plainly it cannot open links | Working as intended | Use the paste fallback |

The paste fallback is on the start page under **My assistant can't open links** —
it carries the whole intake and always works.

## Run it in each terminal

Do this in the terminals your audience actually uses, not just the one on your
machine. They behave differently and the differences are not documented anywhere
you can rely on.

1. Open the start page for the skill you plan to show.
2. Click through to the terminal. In Claude, press enter — it fills the box and
   waits, by design.
3. Check the vendor's funnel for a fresh `guide_read`, and compare the first
   question against the page.
4. Answer the first section normally and confirm the assistant moves to section 2
   from the page rather than inventing one.

Record the result per terminal, with the date. It changes: Copilot lost prompt
prefill entirely in January 2026, and Claude dropped auto-submit in late 2025.

| Terminal | Fetched the guide? | Date | Notes |
|---|---|---|---|
| Claude | | | Fills the box, does not send. Press enter. |
| ChatGPT | | | Sends on arrival. |
| Grok | | | Sign in to grok.com first, or the prompt is lost through the redirect. |
| Copilot | n/a | | No prefill since Jan 2026. Copy the prompt in. |

## If a bank asks "how do you know it read our questions?"

That is the right question and the honest answer is the read log: their guide is
served from our servers, and every fetch of it is recorded against their vendor
account with a timestamp. They can see that the assistant collected their
questions. Say that the fallback exists for the cases where the fetch fails, and
that the connector closes the gap entirely for anyone who adds it — with the
connector the questions never travel at all and the answers are validated
server-side rather than narrated.

Be precise about what the log proves. It proves the page was fetched, not that
every question was asked as written. That second guarantee is the connector's, and
claiming it here is the kind of overstatement a model-risk team will remember.

Do not claim the doc path is enforced. It is instruction-following, and a bank's
model-risk people will find that out faster than you can talk past it.
