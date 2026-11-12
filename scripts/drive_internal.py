#!/usr/bin/env python3
"""Drive the internal portal in headless Chrome.

Same reasoning as `drive_console.py`: the portal is vanilla JS served as static
files, so nothing but a browser will tell us whether it renders. It also checks
the part that matters more than the rendering — that a signed-in account which
is not on the staff list is shown the gate and nothing else.

The API must be running with the staff address configured:

    A2B_STAFF_EMAILS=you@lenni.io .venv/bin/python -m uvicorn a2b.api.main:app --port 8013
    python scripts/drive_internal.py --port 8013 --email you@lenni.io
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from drive_console import CDP, find_chrome, free_port

PASSWORD = "correct-horse-staple"


def account(base: str, email: str, password: str) -> None:
    """Make sure the address can sign in. Signup first, login if it exists."""
    for path in ("/api/auth/signup", "/api/auth/login"):
        body = json.dumps({"email": email, "password": password}).encode()
        req = urllib.request.Request(base + path, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            return
        except urllib.error.HTTPError as exc:
            if path.endswith("login"):
                raise SystemExit(f"could not sign in as {email}: {exc.read().decode()[:200]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8013)
    ap.add_argument("--out", default=".shots")
    ap.add_argument("--email", default="staff@lenni.io")
    ap.add_argument("--outsider", default="outsider@example.com")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{args.port}"

    account(base, args.email, PASSWORD)
    account(base, args.outsider, PASSWORD)

    # A fresh profile every run. Reusing one carries the previous run's session
    # cookie into the first assertion, which is the one checking what somebody
    # without a session can see.
    profile = out / ".chrome-internal"
    shutil.rmtree(profile, ignore_errors=True)

    dport = free_port()
    proc = subprocess.Popen([
        find_chrome(), "--headless=new", f"--remote-debugging-port={dport}",
        "--remote-allow-origins=*", f"--user-data-dir={profile}",
        "--window-size=1440,1000", "--hide-scrollbars", "--no-first-run",
        "--no-default-browser-check", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{dport}/json/list", timeout=1) as r:
                    page = next((t for t in json.load(r) if t["type"] == "page"), None)
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

        def wait_for(expr: str, label: str, timeout: float = 15.0):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if cdp.evaluate(f"!!({expr})"):
                    return
                time.sleep(0.2)
            raise SystemExit(f"timed out waiting for {label}  [{expr}]")

        def open_portal():
            cdp.send("Page.navigate", url=f"{base}/internal/")
            cdp.evaluate("window.__errs=[];"
                         "window.addEventListener('error',e=>window.__errs.push(String(e.message)));"
                         "window.addEventListener('unhandledrejection',"
                         "e=>window.__errs.push('promise: '+e.reason));")
            wait_for("typeof boot==='function'", "portal script")

        def sign_in(email: str):
            cdp.evaluate(f"document.getElementById('g-email').value={json.dumps(email)};"
                         f"document.getElementById('g-pass').value={json.dumps(PASSWORD)}")
            cdp.evaluate("signIn()", await_promise=True)

        steps: list[str] = []

        # The check that matters most, run first and from a real browser: a
        # legitimate account that is not ours sees the gate, not the data.
        open_portal()
        time.sleep(0.8)
        sign_in(args.outsider)
        time.sleep(1.0)
        assert cdp.evaluate("document.querySelector('.view[data-v=\"gate\"]').classList.contains('on')"), \
            "a non-staff account got past the gate"
        assert cdp.evaluate("document.getElementById('nav').classList.contains('hide')"), \
            "the navigation is visible to a non-staff account"
        assert not cdp.evaluate("document.getElementById('companies').textContent.trim().length"), \
            "company data rendered for a non-staff account"
        cdp.screenshot(out / "int-01-gate.png")
        steps.append("a signed-in outsider sees the gate and no data")

        open_portal()
        time.sleep(0.8)
        sign_in(args.email)
        wait_for("document.querySelectorAll('#companies .grid tbody tr').length", "company list")
        rows = cdp.evaluate("document.querySelectorAll('#companies .grid tbody tr').length")
        totals = cdp.evaluate("document.getElementById('totals').textContent")
        assert "companies" in totals, "the totals row did not render"
        cdp.screenshot(out / "int-02-overview.png")
        steps.append(f"overview lists {rows} companies with totals")

        # Straight into a company, the way anyone actually uses this.
        cdp.evaluate("openCompany(0)", await_promise=True)
        wait_for("document.querySelector('.view[data-v=\"company\"]').classList.contains('on')",
                 "company view")
        # Not `.empty`: the loading placeholder is one, so waiting on it matches
        # the spinner and asserts against a half-drawn page.
        wait_for("document.querySelector('#company h2')", "company detail")
        cdp.screenshot(out / "int-03-company.png")
        name = cdp.evaluate("document.querySelector('#company h2').textContent")
        steps.append(f"company drill-in renders ({name})")

        runs = cdp.evaluate("document.querySelectorAll('#company .grid tbody tr[onclick]').length")
        if runs:
            cdp.evaluate("document.querySelector('#company .grid tbody tr[onclick]').click()")
            wait_for("document.querySelector('.view[data-v=\"run\"]').classList.contains('on')",
                     "run view")
            wait_for("document.querySelector('#run .notice')"
                     " || document.getElementById('run').textContent.includes('Could not load')",
                     "run detail")
            assert "recorded against your account" in cdp.evaluate(
                "document.getElementById('run').textContent"), \
                "the run view does not say the visit is recorded"
            cdp.screenshot(out / "int-04-run.png")
            steps.append("a customer's answers open, with the recording notice")

            cdp.evaluate("go('access');loadAccess()", await_promise=True)
            wait_for("document.querySelectorAll('#access .grid tbody tr').length", "access log")
            log = cdp.evaluate("document.getElementById('access').textContent")
            assert args.email in log, "the visit was not written to the access log"
            cdp.screenshot(out / "int-05-access.png")
            steps.append("the visit appears in the access log")
        else:
            steps.append("no customer runs on this instance — run view not exercised")

        cdp.evaluate("go('demand');loadDemand()", await_promise=True)
        wait_for("document.querySelectorAll('#demand .grid, #demand .empty').length", "demand")
        cdp.screenshot(out / "int-06-demand.png")
        steps.append("product demand renders")

        # Handing the session over without reloading. The data one of us was
        # looking at must not still be on the page behind the gate.
        sign_in(args.outsider)
        time.sleep(1.0)
        assert cdp.evaluate("document.querySelector('.view[data-v=\"gate\"]').classList.contains('on')"), \
            "signing in as an outsider left the portal open"
        left = cdp.evaluate("['companies','company','run','access']"
                            ".map(id=>document.getElementById(id).textContent.trim())"
                            ".filter(t=>t.length).length")
        assert not left, "company data was still on the page after access went away"
        steps.append("data is cleared when the session stops being ours")

        errs = cdp.evaluate("JSON.stringify(window.__errs||[])")
        assert errs in ("[]", None), f"JavaScript errors: {errs}"

        print("\nInternal portal walkthrough passed:")
        for s in steps:
            print(f"  - {s}")
        print(f"\nScreenshots in {out}/")
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
