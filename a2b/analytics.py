"""Cross-tenant reporting for the internal portal.

Everything else in this codebase answers questions inside one company. This
module is the exception, and it is kept apart for that reason: nothing here is
reachable from a vendor-facing route, so a mistake in a join cannot end up
serving one bank another bank's numbers.

The queries are grouped rather than per-vendor. A loop calling
`funnel_summary()` once per company reads better and turns a portfolio page into
one query per company per table, which is fine at ten companies and is the kind
of thing that is discovered at four hundred, on the day someone senior is
looking at it.

Timestamps are ISO-8601 UTC strings throughout, which sort lexicographically, so
date filtering is string comparison rather than SQLite date functions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from .db import cursor, loads, new_id, now
from .sessions import list_sessions
from .skills import list_skills

# Events worth putting in front of a person, in the order the customer meets
# them. The table records more than this; the rest is diagnostic and belongs in
# a query, not on a dashboard where every column has to earn its width.
FUNNEL_ORDER = (
    "start_link_viewed",
    "guide_read",
    "session_started",
    "section_completed",
    "skill_completed",
    "delivery",
)


def record_access(email: str, what: str, *, vendor_id: Optional[str] = None,
                  session_id: Optional[str] = None) -> None:
    """Note that one of us looked at something.

    Written for reads, which is unusual and deliberate. The portal shows a
    bank's customers' actual answers; the log is what makes that defensible
    rather than merely convenient.
    """
    with cursor() as cur:
        cur.execute(
            "INSERT INTO staff_access (id, email, vendor_id, session_id, what, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (new_id("acc"), (email or "").lower(), vendor_id, session_id, what, now()),
        )


def access_log(limit: int = 100) -> list[dict]:
    with cursor() as cur:
        return [dict(r) for r in cur.execute(
            "SELECT a.*, v.name AS vendor_name FROM staff_access a"
            " LEFT JOIN vendor v ON v.id = a.vendor_id"
            " ORDER BY a.created_at DESC LIMIT ?", (limit,))]


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _grouped(cur, sql: str, params: tuple = ()) -> dict:
    """`{key: {inner: count}}` from a three-column group-by."""
    out: dict = {}
    for row in cur.execute(sql, params):
        key, inner, count = row[0], row[1], row[2]
        out.setdefault(key, {})[inner] = count
    return out


def platform(days: int = 30) -> dict:
    """Every company on the instance, with enough per row to tell them apart.

    `days` scopes the recent-activity figures only. Totals stay all-time,
    because a portfolio view whose every number moves when you change the date
    filter cannot be read at a glance.
    """
    since = _since(days)
    with cursor() as cur:
        vendors = [dict(r) for r in cur.execute(
            "SELECT id, slug, name, industry_key, website, convey_url, created_at"
            " FROM vendor ORDER BY created_at DESC")]

        skills = _grouped(cur, "SELECT vendor_id, status, COUNT(*) FROM vendor_skill"
                               " GROUP BY vendor_id, status")
        modes = _grouped(cur, "SELECT vendor_id, entry_mode, COUNT(*) FROM vendor_skill"
                              " GROUP BY vendor_id, entry_mode")
        members = _grouped(cur, "SELECT vendor_id, status, COUNT(*) FROM vendor_member"
                                " GROUP BY vendor_id, status")
        runs = _grouped(cur, "SELECT vendor_id, status, COUNT(*) FROM session"
                             " GROUP BY vendor_id, status")
        events = _grouped(cur, "SELECT vendor_id, event, COUNT(*) FROM funnel_event"
                               " GROUP BY vendor_id, event")
        recent = _grouped(cur, "SELECT vendor_id, event, COUNT(*) FROM funnel_event"
                               " WHERE created_at >= ? GROUP BY vendor_id, event", (since,))
        last = {r[0]: r[1] for r in cur.execute(
            "SELECT vendor_id, MAX(created_at) FROM funnel_event GROUP BY vendor_id")}

        # Not a per-vendor number, and the one that would have caught #86 months
        # earlier: a product created in a fetch-based mode still looks healthy
        # from inside the company that owns it.
        by_mode = {r[0]: r[1] for r in cur.execute(
            "SELECT entry_mode, COUNT(*) FROM vendor_skill GROUP BY entry_mode")}
        series = [{"day": r[0], "events": r[1]} for r in cur.execute(
            "SELECT substr(created_at,1,10), COUNT(*) FROM funnel_event"
            " WHERE created_at >= ? GROUP BY 1 ORDER BY 1", (since,))]

    rows = []
    for v in vendors:
        skill = skills.get(v["id"], {})
        run = runs.get(v["id"], {})
        event = events.get(v["id"], {})
        rows.append({
            **v,
            "products": sum(skill.values()),
            "published": skill.get("published", 0),
            "entry_modes": modes.get(v["id"], {}),
            "members": sum(members.get(v["id"], {}).values()),
            "invited": members.get(v["id"], {}).get("invited", 0),
            "runs": {"total": sum(run.values()), **run},
            "events": event,
            "recent_events": sum(recent.get(v["id"], {}).values()),
            "last_active": last.get(v["id"]),
            # The question a portfolio view exists to answer. A company with
            # products and no runs is not a customer yet, however good the
            # workspace looks.
            "live": bool(event.get("session_started")),
        })

    return {
        "window_days": days,
        "companies": rows,
        "totals": {
            "companies": len(rows),
            "live_companies": sum(1 for r in rows if r["live"]),
            "products": sum(r["products"] for r in rows),
            "published": sum(r["published"] for r in rows),
            "runs": sum(r["runs"]["total"] for r in rows),
            "completed": sum(r["runs"].get("complete", 0) for r in rows),
            "deliveries": sum(r["events"].get("delivery", 0) for r in rows),
        },
        "entry_modes": by_mode,
        "series": series,
    }


def _section_dropoff(skill: dict, runs: list[dict]) -> list[dict]:
    """How far customers get through one product, section by section.

    Read from each run's own section status rather than from the completion
    events, because a vendor who deletes a section leaves events behind that
    point at nothing. The run's status is reconciled against the live sections,
    so it answers the question the way the customer experienced it.
    """
    mine = [r for r in runs if r["skill_id"] == skill["id"]]
    out = []
    for section in skill["sections"]:
        done = sum(1 for r in mine
                   if (r.get("section_status") or {}).get(section["id"]) == "complete")
        out.append({
            "id": section["id"],
            "name": section["name"],
            "position": section["position"],
            "completed": done,
            "of": len(mine),
        })
    return out


def company(vendor_id: str, limit: int = 50) -> Optional[dict]:
    """One company in full: what they built, and what their customers did with it."""
    with cursor() as cur:
        vendor = cur.execute("SELECT * FROM vendor WHERE id=?", (vendor_id,)).fetchone()
        if not vendor:
            return None
        vendor = dict(vendor)
        per_skill = _grouped(cur, "SELECT skill_id, event, COUNT(*) FROM funnel_event"
                                  " WHERE vendor_id=? GROUP BY skill_id, event", (vendor_id,))
        members = [dict(r) for r in cur.execute(
            "SELECT email, role, status, created_at FROM vendor_member"
            " WHERE vendor_id=? ORDER BY created_at", (vendor_id,))]
        recent_events = [dict(r) for r in cur.execute(
            "SELECT event, skill_id, session_id, detail_json, created_at FROM funnel_event"
            " WHERE vendor_id=? ORDER BY created_at DESC LIMIT ?", (vendor_id, limit))]

    for e in recent_events:
        e["detail"] = loads(e.pop("detail_json"), {})

    runs = list_sessions(vendor_id, limit=200)
    products = []
    for skill in list_skills(vendor_id):
        products.append({
            "id": skill["id"],
            "name": skill["name"],
            "slug": skill["slug"],
            "status": skill["status"],
            "entry_mode": skill["entry_mode"],
            "sections": len(skill["sections"]),
            "destination": skill["destination"],
            "published_at": skill["published_at"],
            "updated_at": skill["updated_at"],
            "events": per_skill.get(skill["id"], {}),
            "dropoff": _section_dropoff(skill, runs),
        })

    return {
        "vendor": vendor,
        "products": products,
        "members": members,
        "runs": runs[:limit],
        "recent_events": recent_events,
    }
