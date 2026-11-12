#!/usr/bin/env python3
"""Move every skill in the database onto one entry mode, and recompile.

    python scripts/set_entry_mode_all.py inline
    python scripts/set_entry_mode_all.py inline --dry-run

Entry mode is stored per skill so a vendor can make the trade product by
product, which means there is no deployment-wide switch to flip when the default
changes. The startup recompile does not help: it rebuilds each prompt *in the
mode the row already names*, so a skill left on `doc` keeps emitting a doc
prompt no matter how many times the server restarts.

Deliberately not a migration in `db.py`. Those run once, unattended, on every
deployment — which is right for filling in a column nobody has chosen yet, and
wrong for overwriting a choice a vendor made on purpose. This is the same
operation with a human deciding when it happens.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from a2b import skills  # noqa: E402
from a2b.compiler import ENTRY_MODES  # noqa: E402
from a2b.config import DB_PATH  # noqa: E402
from a2b.db import init_db  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=ENTRY_MODES)
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would change and touch nothing")
    args = ap.parse_args()

    init_db()
    print(f"database: {DB_PATH}\n")

    changed = kept = 0
    for vendor in skills.list_vendors():
        for skill in skills.list_skills(vendor["id"]):
            was = skill["entry_mode"]
            if was == args.mode:
                kept += 1
                continue
            changed += 1
            mark = "would set" if args.dry_run else "set"
            print(f"  {mark}  {vendor['slug']}/{skill['slug']}  {was} -> {args.mode}")
            if not args.dry_run:
                skills.set_entry_mode(skill["id"], args.mode)

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{verb} {changed}, already {args.mode}: {kept}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
