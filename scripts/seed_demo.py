#!/usr/bin/env python3
"""Seed the Bluebonnet Community Bank scenario from the demo.

Gives you the same starting point as `a2b-demo.html`: a bank, six offerings
scanned off a website, five of them matched to templates and one ("Trust
Services") with no template, which becomes a custom skill.

    python scripts/seed_demo.py            # create it, or top up what is there
    python scripts/seed_demo.py --reset    # wipe the database first

Running it twice is safe. It used to create a second bank every time, because
`create_vendor` deduplicates the slug rather than refusing — so a server that had
been deployed with `--seed` a few times ended up with Bluebonnet Community Bank
`-7`, `-8` and `-9` and no way to tell which one the demo links pointed at.

It also creates an account to sign in with, because the console is behind a
login now and a seeded workspace nobody can open is not much of a demo.

Everything it seeds ends up published, delivered through Convey, and marked
connector-only, because a draft is invisible to the connector — `lenni_start_session`
refuses one — and a demo whose first tool call fails is not a demo.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from a2b import auth, ingest, registry, sessions, skills  # noqa: E402
from a2b.config import DB_PATH  # noqa: E402
from a2b.db import init_db  # noqa: E402

DEMO_EMAIL = "sarah@bluebonnetbank.com"

# Fixed rather than derived, so a second run finds the first run's bank instead
# of minting a near-identical one beside it.
DEMO_SLUG = "bluebonnet-community-bank"
CONVEY_URL = "https://bluebonnet.convey.lenni.io"

# The bundled stand-in for the live site, shared with the console's scan flow so
# both walk the same HTML. Bank-marketing voice on purpose — that is the register
# the matcher has to cope with.
BLUEBONNET_HTML = ingest.demo_fixture("bluebonnetbank.com")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete the database first")
    args = ap.parse_args()

    if args.reset and DB_PATH.exists():
        DB_PATH.unlink()
        for suffix in ("-wal", "-shm"):
            p = DB_PATH.with_name(DB_PATH.name + suffix)
            if p.exists():
                p.unlink()
        print(f"removed {DB_PATH}")

    init_db()

    vendor = skills.get_vendor_by_slug(DEMO_SLUG)
    if vendor:
        vendor_id = vendor["id"]
        print(f"vendor: {vendor['name']}  ({vendor['slug']})  — already here, reusing")
    else:
        vendor_id = skills.create_vendor(
            name="Bluebonnet Community Bank",
            industry_key="banking",
            website="bluebonnetbank.com",
            slug=DEMO_SLUG,
            description=(
                "Community bank in Boerne, TX (est. 1949, Member FDIC). Commercial, consumer "
                "and construction lending across the Texas Hill Country."
            ),
        )
        vendor = skills.get_vendor(vendor_id)
        print(f"vendor: {vendor['name']}  ({vendor['slug']})")

    # A demo password is fine on a laptop and not fine on a server, so the
    # server sets its own and the script only ever prints what it was given.
    password = os.environ.get("A2B_DEMO_PASSWORD", "bluebonnet-demo-2026")
    if auth.user_exists(DEMO_EMAIL):
        print(f"login: {DEMO_EMAIL} (already exists, password unchanged)")
    else:
        auth.create_user(DEMO_EMAIL, password, "Sarah Whitley")
        shown = password if "A2B_DEMO_PASSWORD" not in os.environ else "(from A2B_DEMO_PASSWORD)"
        print(f"login: {DEMO_EMAIL} / {shown}")
    skills.add_member(vendor_id, DEMO_EMAIL, role="admin", status="active")

    if skills.list_skills(vendor_id):
        print("skills already seeded — skipping the scan")
        return _make_demo_ready(vendor_id, vendor)

    scan = ingest.scan_html("https://bluebonnetbank.com", BLUEBONNET_HTML)
    ranked = ingest.detect_industry(scan)
    industry = ranked[0][0] if ranked else "banking"
    matches = ingest.match_scan(scan, industry)
    run_id = ingest.save_run(vendor_id, scan, industry, matches)
    print(f"industry suggested: {industry}")
    print(f"scan found {len(matches)} offerings:")
    for m in matches:
        target = m.product_name or "— no template —"
        print(f"  {m.confidence:9s} {m.found_label[:38]:40s} -> {target}")

    run = ingest.get_run(run_id)
    ingest.confirm_industry(run_id, industry)
    # Confirm everything that matched, plus Trust Services as a custom skill —
    # the same six-row confirmation the demo walks through.
    confirm = [m["id"] for m in run["matches"] if m["confidence"] != ingest.NONE
               or "trust" in m["found_label"].lower()]
    created = ingest.confirm_matches(run_id, vendor_id, confirm)
    print(f"\nconfirmed {len(created)} offerings -> draft skills")

    return _make_demo_ready(vendor_id, vendor)


def _make_demo_ready(vendor_id: str, vendor: dict) -> int:
    """Give every skill somewhere to deliver, publish it, and route it at the tools.

    Publishing all of them rather than only the flagship is the point: the demo is
    a bank with a shelf of products, and four of the five buttons landing on a
    draft makes it a demo of one product. A draft is invisible to the connector —
    `lenni_start_session` refuses one by design — so this is what stands between a
    seeded server and a working tool call.
    """
    print("\nmaking every skill demo-ready")
    for s in skills.list_skills(vendor_id):
        skills.connect_convey(s["id"], CONVEY_URL)
        if s["status"] != "published":
            skills.publish_skill(s["id"])
        # Inline, not connector-only: a seeded server has to demo on a laptop
        # where nobody has added anything and whose assistant may not fetch a URL
        # at all. The connector stays configured, so a machine that does have it
        # still shows the upgrade.
        skills.set_entry_mode(s["id"], "inline")

    print("\nskills:")
    for s in skills.list_skills(vendor_id):
        pin = s["template_status"]["pinned_version"]
        pin_txt = f"pinned v{pin}" if pin else "custom"
        route = s["entry_mode"]
        print(f"  [{s['status']:9s}] {s['name']:34s} {len(s['sections'])} sections  "
              f"{pin_txt:12s} {route}")

    print(f"\nvendor_id: {vendor_id}")
    print("console:   http://127.0.0.1:8000/console/")
    print(f"agents:    http://127.0.0.1:8000/v/{vendor['slug']}/agents")
    print(f"llms.txt:  http://127.0.0.1:8000/v/{vendor['slug']}/llms.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
