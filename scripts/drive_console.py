#!/usr/bin/env python3
"""Drive the vendor console in headless Chrome and capture each screen.

Verifies the console against a running API rather than eyeballing it: every step
asserts on what the page actually shows, and any JavaScript error fails the run.

    python scripts/drive_console.py --port 8011 --out .shots
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "",
    shutil.which("chromium") or "",
]


def find_chrome() -> str:
    for p in CHROME_PATHS:
        if p and Path(p).exists():
            return p
    raise SystemExit("Chrome not found — install Google Chrome or set CHROME_PATH.")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CDP:
    """Minimal Chrome DevTools Protocol client over a websocket."""

    def __init__(self, ws_url: str):
        from websocket import create_connection  # type: ignore
        self.ws = create_connection(ws_url, timeout=30)
        self.n = 0

    def send(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def evaluate(self, expr: str, await_promise: bool = False):
        out = self.send("Runtime.evaluate", expression=expr, awaitPromise=await_promise,
                        returnByValue=True)
        result = out.get("result", {})
        if out.get("exceptionDetails"):
            raise RuntimeError(f"JS error: {out['exceptionDetails'].get('text')} "
                               f"{result.get('description', '')}")
        return result.get("value")

    def screenshot(self, path: Path):
        data = self.send("Page.captureScreenshot", captureBeyondViewport=True)["data"]
        path.write_bytes(base64.b64decode(data))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--out", default=".shots")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"

    dport = free_port()
    profile = out / ".chrome-profile"
    proc = subprocess.Popen([
        find_chrome(), "--headless=new", f"--remote-debugging-port={dport}",
        # Recent Chrome rejects DevTools websockets whose Origin it does not
        # recognise, which is every client that is not DevTools itself.
        "--remote-allow-origins=*",
        f"--user-data-dir={profile}", "--window-size=1440,1000",
        "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{dport}/json/list", timeout=1) as r:
                    tabs = json.load(r)
                page = next((t for t in tabs if t["type"] == "page"), None)
                if page:
                    ws_url = page["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.25)
        if not ws_url:
            raise SystemExit("Could not reach Chrome DevTools.")

        cdp = CDP(ws_url)
        cdp.send("Page.enable")
        cdp.send("Runtime.enable")
        # Any uncaught error should fail the run rather than hide behind a
        # screenshot that happens to look fine.
        cdp.evaluate("window.__errs=[];"
                     "window.addEventListener('error',e=>window.__errs.push(String(e.message)));"
                     "window.addEventListener('unhandledrejection',"
                     "e=>window.__errs.push('promise: '+e.reason));")

        def wait_for(expr: str, label: str, timeout: float = 15.0):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if cdp.evaluate(f"!!({expr})"):
                    return
                time.sleep(0.2)
            raise SystemExit(f"timed out waiting for {label}  [{expr}]")

        steps: list[str] = []

        def watch_errors():
            """Collect page errors.

            Re-armed after every navigation: signing in is a real page load
            now, and listeners do not survive one. Forgetting this would not
            fail the run — it would quietly stop checking, which is worse.
            """
            cdp.evaluate("window.__errs=window.__errs||[];"
                         "window.addEventListener('error',e=>window.__errs.push(String(e.message)));"
                         "window.addEventListener('unhandledrejection',"
                         "e=>window.__errs.push('promise: '+e.reason));")

        cdp.send("Page.navigate", url=f"{base}/console/")
        watch_errors()
        # `let` at script top level is a global binding but not a window
        # property, so this has to be a bare reference rather than window.APPS.
        wait_for("document.getElementById('url-in') && typeof APPS!=='undefined' && APPS.length",
                 "console boot")
        time.sleep(0.6)
        cdp.screenshot(out / "01-home.png")
        steps.append("marketing homepage renders")

        # The authoring API is closed to anonymous callers. Check that from the
        # page itself, so a regression that reopens it fails the build.
        anon = cdp.evaluate(
            "fetch('/api/vendors').then(r=>r.status)", await_promise=True)
        assert anon == 401, f"anonymous /api/vendors should be 401, got {anon}"
        steps.append("anonymous access to the authoring API is refused (401)")

        # Sign-in lives at its own address now, so this is a genuine page load
        # and back again rather than a modal. Walking it the way a person does
        # is the only way the redirect back is actually covered.
        cdp.send("Page.navigate", url=f"{base}/login")
        wait_for("document.getElementById('email')", "login page")
        time.sleep(0.3)
        cdp.screenshot(out / "01b-login.png")
        steps.append("sign-in page renders at /login")

        cdp.send("Page.navigate", url=f"{base}/forgot-password")
        wait_for("document.getElementById('email')", "forgot page")
        time.sleep(0.3)
        cdp.screenshot(out / "01c-forgot.png")
        steps.append("password reset page renders at /forgot-password")

        # A fresh email each run, because the database persists between runs.
        email = f"drive-{int(time.time())}@bluebonnetbank.com"
        cdp.send("Page.navigate", url=f"{base}/signup")
        wait_for("document.getElementById('email')", "signup page")
        watch_errors()
        cdp.evaluate(
            f"document.getElementById('email').value={json.dumps(email)};"
            "document.getElementById('name').value='Drive Bot';"
            "document.getElementById('password').value='drive-console-secret';"
            "document.getElementById('f').dispatchEvent("
            "new Event('submit',{cancelable:true}));"
        )
        # The page redirects to the console on success, so waiting on the
        # console's own globals proves both halves: the account was created and
        # the browser was handed back with a session.
        wait_for("typeof USER!=='undefined' && USER && document.getElementById('url-in')",
                 "signed in and back in the console", timeout=30)
        watch_errors()
        who = cdp.evaluate("USER.email")
        assert who == email, f"signed in as {who!r}, expected {email!r}"
        steps.append(f"signed up at /signup and landed back in the console as {email}")

        # The scan animation is ~5s before the real result lands.
        cdp.evaluate("startScan()")
        wait_for("document.getElementById('v-industry').classList.contains('on')",
                 "industry confirm", timeout=30)
        time.sleep(0.5)
        cdp.screenshot(out / "02-industry.png")
        industry = cdp.evaluate("document.querySelector('#v-industry .indpick b').textContent")
        # Against the registry's own wording rather than a copy of it. This read
        # `== "Banking"` and the industry has been called "Banking / Lending"
        # since the first commit, so the step could only ever fail — which is
        # its own lesson about a check nobody runs.
        expected = cdp.evaluate(
            "(INDUSTRIES.find(i=>i.key==='banking')||{}).name")
        assert industry.strip() == expected, f"expected {expected!r}, got {industry!r}"
        steps.append(f"industry suggested = {industry.strip()}")

        cdp.evaluate("go('v-products')")
        wait_for("document.getElementById('v-products').classList.contains('on')", "match screen")
        time.sleep(0.4)
        cdp.screenshot(out / "03-matches.png")
        rows = cdp.evaluate("document.querySelectorAll('#match-list .matchrow').length")
        high = cdp.evaluate("document.querySelectorAll('#match-list .pill.live').length")
        text = cdp.evaluate("document.getElementById('match-list').textContent")
        assert rows >= 6, f"expected 6+ match rows, got {rows}"
        # Guards against the rows rendering off the wrong response fields, which
        # looks like a plausible screen until you read it.
        assert high >= 5, f"expected 5+ high-confidence matches, got {high}"
        assert "undefined" not in text, "a match row rendered an undefined field"
        assert "Multifamily" in text, "the flagship match is missing"
        steps.append(f"{rows} offerings proposed, {high} at high confidence")

        cdp.evaluate("buildWorkspace()", await_promise=True)
        wait_for("document.getElementById('v-ws').classList.contains('on')", "workspace")
        wait_for("document.querySelectorAll('#prod-list .prod').length > 0", "skill list")
        time.sleep(0.4)
        cdp.screenshot(out / "04-workspace.png")
        count = cdp.evaluate("document.querySelectorAll('#prod-list .prod').length")
        steps.append(f"{count} draft skills created")

        cdp.evaluate("openProduct(0)", await_promise=True)
        wait_for("document.querySelectorAll('#sec-list .sec').length > 0", "skill editor")
        cdp.evaluate("togEntry()")
        time.sleep(0.5)
        cdp.screenshot(out / "05-editor.png")
        prompt = cdp.evaluate("document.getElementById('ed-prompt').textContent")
        assert "Section 1 of" in prompt, "entry prompt missing the section-1 header"
        # The prompt has to carry the vendor's own questions. Naming the sections
        # and telling the AI to fetch the rest over WebMCP is what sent a customer
        # into an interview the vendor never wrote.
        assert "WebMCP" not in prompt, "entry prompt still tells the AI to dial WebMCP"
        first_ask = cdp.evaluate(
            "PRODUCTS[curP].secs[0].asks && PRODUCTS[curP].secs[0].asks[0]")
        assert first_ask, "first section has no questions to check against"
        assert first_ask in prompt, f"entry prompt lost the first question: {first_ask!r}"

        # And every later question too. This used to assert the opposite —
        # section 1 only, the rest fetched — which was right until live testing
        # showed that asking a terminal to fetch anything is the one part we do
        # not control. Inline carries the lot so there is nothing to retrieve.
        later = json.loads(cdp.evaluate(
            "JSON.stringify(PRODUCTS[curP].secs.slice(1).flatMap(s=>s.asks||[]))"))
        missing = [q for q in later if q not in prompt]
        assert not missing, f"later sections did not travel in the prompt: {missing[:2]}"
        # Carrying them is not the same as showing them early, so the pacing
        # rule has to be there as well.
        assert "do not show me a later section" in prompt.lower(), \
            "the prompt carries every section but never tells the AI to pace them"
        steps.append(f"entry prompt carries the whole intake "
                     f"({len(later) + 1} questions, paced)")

        # The wording editor. Worth driving rather than unit-testing alone: the
        # save path reaches two endpoints and re-renders three panels, and the
        # failure it is here to catch is the console showing a vendor their old
        # text after a successful save.
        cdp.evaluate("editEntry()")
        time.sleep(0.3)
        assert cdp.evaluate("!!document.getElementById('ep-pacing')"), \
            "the wording editor did not open"
        assert cdp.evaluate("!!document.getElementById('fp-deliver')"), \
            "the closing message did not move into the entry prompt editor"
        cdp.screenshot(out / "05b-prompt-editor.png")

        mine = "Ask me one section at a time — I am usually on a phone."
        cdp.evaluate(f"document.getElementById('ep-pacing').value={json.dumps(mine)}")
        cdp.evaluate("saveEntry()", await_promise=True)
        wait_for("!document.getElementById('ep-pacing')", "editor to close")
        prompt = cdp.evaluate("document.getElementById('ed-prompt').textContent")
        assert mine in prompt, "the vendor's wording never reached the compiled prompt"
        assert first_ask in prompt, "editing the wording dropped the vendor's questions"
        assert "not a credit decision" in prompt.lower(), \
            "editing the wording dropped the guardrail"

        cdp.evaluate("resetEntry()", await_promise=True)
        time.sleep(0.4)
        prompt = cdp.evaluate("document.getElementById('ed-prompt').textContent")
        assert mine not in prompt, "reset left the vendor's wording in place"
        steps.append("prompt wording edits, saves, recompiles and resets")

        cdp.evaluate("togSec(0)")
        time.sleep(0.4)
        cdp.screenshot(out / "06-section-open.png")
        says = cdp.evaluate("document.querySelector('#sec-list .say').textContent.trim().length")
        assert says > 40, "section opening looks empty"
        steps.append("section script renders")

        # Core sections must refuse deletion.
        cdp.evaluate("delSec(0)", await_promise=True)
        time.sleep(1.0)
        toast = cdp.evaluate("document.getElementById('toast').textContent")
        assert "core section" in toast.lower(), f"core delete not refused: {toast!r}"
        steps.append("core section delete refused")

        # Destination + publish.
        cdp.evaluate("setDest({mode:'link'})", await_promise=True)
        time.sleep(0.5)
        cdp.evaluate("setDest({link:'https://bluebonnet.example/upload'})", await_promise=True)
        time.sleep(0.6)
        cdp.screenshot(out / "07-destination.png")
        assert cdp.evaluate("PRODUCTS[curP].canPublish"), "publish still blocked"
        steps.append("destination set, publish unblocked")

        cdp.evaluate("publishSkill()", await_promise=True)
        time.sleep(1.2)
        cdp.screenshot(out / "08-published.png")
        assert cdp.evaluate("PRODUCTS[curP].status") == "published", "skill did not publish"
        steps.append("skill published")

        # The start links must be real anchors with real hrefs. The bug this
        # guards against is a row that looks like a button and does nothing when
        # a customer clicks it.
        rows = cdp.evaluate("document.querySelectorAll('#start-links .startrow').length")
        anchors = cdp.evaluate(
            "JSON.stringify([...document.querySelectorAll('#start-links a.startlink')]"
            ".map(a=>({href:a.getAttribute('href'),target:a.target,text:a.textContent.trim()})))")
        links = json.loads(anchors)
        assert rows == 5, f"expected 5 start-link rows, got {rows}"
        for a in links:
            assert a["href"] and a["href"] != "#", f"dead start link: {a}"
            assert a["target"] == "_blank", f"start link does not open a new tab: {a}"
        hosts = sorted({a["href"].split("/")[2] for a in links})
        assert "claude.ai" in hosts, f"Claude is not linked: {hosts}"

        # Terminals that cannot take a prompt from a link must render as copy
        # rows, not anchors — Copilot dropped prefill and a link there opens an
        # empty chat, which looks like it worked.
        unsupported = json.loads(cdp.evaluate(
            "JSON.stringify((PRODUCTS[curP].links.terminals||[])"
            ".filter(t=>!t.supported).map(t=>t.key))"))
        assert len(links) == 5 - len(unsupported), (
            f"expected {5 - len(unsupported)} openable rows, got {len(links)}")
        for key in unsupported:
            copy_row = cdp.evaluate(
                f"!!document.querySelector('#start-links div.startlink[onclick*=\"{key}\"]')")
            assert copy_row, f"{key} has no copy row to fall back on"

        # The prompt has to arrive whole; a mis-encoded one is cut at the first &.
        prompt_ok = cdp.evaluate(
            "(()=>{const a=document.querySelector('#start-links a.startlink');"
            "const q=new URL(a.href).searchParams.get('q');"
            "return q===PRODUCTS[curP].entry;})()")
        assert prompt_ok, "the prompt in the deep link does not match the compiled prompt"
        # And it has to be the vendor's workflow, not a pointer to it.
        assert cdp.evaluate(
            "PRODUCTS[curP].entry.includes(PRODUCTS[curP].secs[0].asks[0])"
        ), "the deep link carries a prompt without the vendor's questions"
        cdp.screenshot(out / "08b-start-links.png")
        steps.append(f"{len(links)} start links open real terminals ({', '.join(hosts)}); "
                     f"{len(unsupported)} fall back to copy")

        cdp.evaluate("wsGo('agent');renderAgent()", await_promise=True)
        time.sleep(1.2)
        cdp.screenshot(out / "09-agent-page.png")
        llms = cdp.evaluate("document.getElementById('llms-block').textContent")
        assert "## Services" in llms, "llms.md did not render"
        steps.append("agent page regenerated from the real manifest")

        cdp.evaluate("wsGo('site');renderSite()")
        time.sleep(1.0)
        cdp.screenshot(out / "10-website-preview.png")
        steps.append("website preview rendered")

        cdp.evaluate("wsGo('store');renderStore()")
        time.sleep(0.6)
        cdp.screenshot(out / "11-app-store.png")
        steps.append("app store rendered")

        # The scanner shipped with the method missing from its POST, so the
        # browser refused to send the request and every bank in the shortlist
        # reported a failure that looked like the bank blocking us. Nothing in
        # the Python suite could have caught it — the bug was three characters
        # of JavaScript — which is what this step is for.
        #
        # `bluebonnetbank.com` resolves to the bundled fixture rather than the
        # network, so this asserts the wiring without depending on a real bank's
        # marketing site being up and unchanged.
        cdp.evaluate("wsGo('scan');initScanner()", await_promise=True)
        wait_for("document.querySelectorAll('#scan-shortlist .scan-pick').length===5",
                 "bank shortlist")

        # Two ids in the document were `scan-url` — a div in the onboarding view
        # and this input — so `getElementById` returned the div, `runScan` read
        # `undefined` off it and stopped at its own "enter a website" guard. A
        # typed URL could not be scanned by anyone.
        #
        # This step passed throughout, because it set `.value` from JavaScript
        # exactly as the shortlist buttons do, and assigning `.value` to a div
        # stores it as an ordinary property that reads straight back. So the
        # value is now typed into the element the user types into, found the way
        # the user finds it, and the uniqueness of the id is asserted outright.
        dupes = cdp.evaluate(
            "(()=>{const seen={},bad=[];"
            "document.querySelectorAll('[id]').forEach(e=>{"
            "if(seen[e.id])bad.push(e.id);seen[e.id]=1});return bad.join(',')})()")
        assert not dupes, f"duplicate element ids in the document: {dupes}"

        cdp.evaluate(
            "(()=>{const i=document.querySelector('input[placeholder^=\"bank.com\"]');"
            "i.value='bluebonnetbank.com';i.dispatchEvent(new Event('input'))})()")
        cdp.evaluate("runScan()")
        wait_for("document.querySelectorAll('#scan-out .scan-item').length",
                 "scan results", timeout=60)
        time.sleep(0.4)
        cdp.screenshot(out / "11c-scanner.png")
        found = cdp.evaluate("document.querySelectorAll('#scan-out .scan-item').length")
        scan_text = cdp.evaluate("document.getElementById('scan-out').textContent")
        assert "did not run" not in scan_text, f"scan failed: {scan_text[:300]}"
        assert "Enter a website" not in scan_text, "the scan never left the page"
        assert found >= 3, f"expected a catalog, got {found} offerings"
        steps.append(f"website scanner read a typed URL and found {found} offerings")

        # The sidebar used to say "Not signed in". It should now name the
        # creator, as an admin, and list them as the first member.
        cdp.evaluate("wsGo('team');loadTeam()", await_promise=True)
        time.sleep(0.8)
        cdp.screenshot(out / "11b-team.png")
        who_name = cdp.evaluate("document.getElementById('who-name').textContent")
        who_role = cdp.evaluate("document.getElementById('who-role').textContent")
        team = cdp.evaluate("document.getElementById('team-list').textContent")
        assert who_name == "Drive Bot", f"sidebar shows {who_name!r}"
        assert who_role == "admin", f"creator should be admin, got {who_role!r}"
        assert email in team, "the creator is not listed as a member"
        steps.append(f"signed-in identity shown as {who_name} ({who_role})")

        # The preview makes two MCP round trips per section, so wait on the
        # closing line rather than guessing at a duration.
        cdp.evaluate("openPreview()")
        wait_for("/✅|The run stopped/.test(document.getElementById('cp-thread').innerHTML)",
                 "customer run", timeout=30)
        time.sleep(0.5)
        cdp.screenshot(out / "12-customer-run.png")
        modal_text = cdp.evaluate("document.getElementById('mbox').textContent")
        assert "The run stopped" not in modal_text, f"customer run failed: {modal_text[-300:]}"
        assert "complete" in modal_text.lower(), f"customer run did not finish: {modal_text[-300:]}"
        steps.append("customer run chained through every section over real MCP")

        # Signing out, and staying out. The second half is the part worth
        # checking: a sign-out that leaves the cookie alive looks identical
        # from the page it redirects to.
        cdp.send("Page.navigate", url=f"{base}/logout")
        wait_for("document.getElementById('email')", "back at the sign-in page")
        watch_errors()
        session = cdp.evaluate(
            "fetch('/api/auth/me').then(r=>r.json()).then(d=>d.user?'still signed in':'signed out')",
            await_promise=True)
        assert session == "signed out", f"after /logout the session was {session!r}"
        locked = cdp.evaluate("fetch('/api/vendors').then(r=>r.status)", await_promise=True)
        assert locked == 401, f"authoring API should be 401 after logout, got {locked}"
        steps.append("signed out, and the authoring API is closed again (401)")

        errs = cdp.evaluate("window.__errs")
        if errs:
            print("\nJavaScript errors:")
            for e in errs:
                print("  -", e)
            return 1

        print("\nConsole walkthrough passed:")
        for s in steps:
            print("  -", s)
        print(f"\nScreenshots in {out}/")
        return 0
    finally:
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
