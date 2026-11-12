#!/usr/bin/env python3
"""Turn an organisation's live start links on by writing their first rows.

The workspace overlay only keeps rows current for organisations that already
have some, so that editing a scanned prospect never puts a prompt in a real
bank's name behind a lenni.io address. This writes the first ones.

    python scripts/seed_live_links.py payloads.json [--actor you@example.com]

`payloads.json` is a list of {"vendor": slug, "product": slug, "body": {...}},
where `body` is exactly what the overlay would send. Produce it in a browser on
a staging or sandbox page, so it comes from the same demo build the workspace
runs:

    await window.__A2B_LIVE__.payloadFor("bluebonnet-community-bank", "consumer-auto")

Writes straight to the database rather than through the API, because the
write route wants a staff session and this runs on the server.

Running it twice is safe: a row that already says the same thing is left
alone and logs nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from a2b import live  # noqa: E402
from a2b.db import init_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("payloads", type=Path)
    ap.add_argument("--actor", default="seed@lenni.io",
                    help="recorded as who made the change")
    args = ap.parse_args()

    entries = json.loads(args.payloads.read_text(encoding="utf-8"))
    init_db()
    failed = 0
    for entry in entries:
        vendor, product, body = entry["vendor"], entry["product"], entry["body"]
        try:
            row = live.put(
                vendor, product,
                vendor_name=body["vendor_name"], product_name=body["product_name"],
                status=body["status"], prompt=body.get("prompt", ""),
                notice=body.get("notice", ""), page=body.get("page") or {},
                actor_email=args.actor,
            )
        except (KeyError, ValueError) as exc:
            failed += 1
            print(f"refused  {vendor}/{product}: {exc}")
            continue
        state = "written" if row["changed"] else "same    "
        print(f"{state} {vendor}/{product}  v{row['version']}  {row['status']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
