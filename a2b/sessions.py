"""Runtime: customer profiles, sessions, and section chaining.

A session is one run of one skill by one customer. It advances section by
section; each completed section causes the customer's AI to call back for the
next one. The vendor never sees this plumbing — in their editor it reads as
"moves on to section 2 — Deal" — but it is real here.

`section_status` is the field that makes it work, and it is deliberately keyed
by section id rather than index. A vendor can reorder or insert sections while a
customer is mid-run; an index-keyed status would silently point at the wrong
section after that.

Profiles are email-keyed and NOT scoped to a vendor, which is what would make a
customer's second skill faster than their first. That reuse is currently switched
off, and the reason is worth reading before switching it back on.

An email address is something other people know. While a session inherited a
profile's facts on the strength of an email alone, anyone who could reach the
public MCP endpoint could start a session as somebody else and read out whatever
that person had told any vendor on the platform — and, because saving wrote back
to the profile, could edit it too. Nothing here proved the caller owned the
address, so nothing here could safely act on it.

So a session now starts empty and keeps its facts to itself. The profile is still
created and still linked, because the vendor needs to know who they are talking
to; it is only the reading and writing of a *pre-existing* profile that waits for
proof of the address. #15 is that proof, and #24 is the hole this closes.
"""
from __future__ import annotations

from typing import Any, Optional

from .compiler import compile_final_prompt, destination_label
from .db import cursor, dumps, loads, new_id, now
from .skills import get_skill, get_skill_by_slug, get_vendor

PENDING, ACTIVE, COMPLETE = "pending", "active", "complete"


# --------------------------------------------------------------------------
# Profiles
# --------------------------------------------------------------------------

def upsert_profile(email: str, name: Optional[str] = None,
                   facts: Optional[dict] = None) -> dict:
    email = (email or "").strip().lower()
    if not email:
        raise ValueError("a profile needs an email — it is the key")
    with cursor() as cur:
        row = cur.execute("SELECT * FROM customer_profile WHERE email=?", (email,)).fetchone()
        if row:
            merged = loads(row["facts_json"], {})
            merged.update(facts or {})
            cur.execute(
                "UPDATE customer_profile SET name=COALESCE(?, name), facts_json=?, updated_at=?"
                " WHERE id=?",
                (name, dumps(merged), now(), row["id"]),
            )
            pid = row["id"]
        else:
            pid = new_id("cus")
            cur.execute(
                "INSERT INTO customer_profile (id, email, name, facts_json, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (pid, email, name, dumps(facts or {}), now(), now()),
            )
    return get_profile(pid)


def get_profile(profile_id: str) -> Optional[dict]:
    with cursor() as cur:
        row = cur.execute("SELECT * FROM customer_profile WHERE id=?", (profile_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["facts"] = loads(d.pop("facts_json"), {})
    return d


def get_profile_by_email(email: str) -> Optional[dict]:
    with cursor() as cur:
        row = cur.execute(
            "SELECT id FROM customer_profile WHERE email=?", ((email or "").strip().lower(),)
        ).fetchone()
    return get_profile(row["id"]) if row else None


# --------------------------------------------------------------------------
# Funnel
# --------------------------------------------------------------------------

def record_event(event: str, *, session_id: Optional[str] = None,
                 vendor_id: Optional[str] = None, skill_id: Optional[str] = None,
                 detail: Optional[dict] = None) -> None:
    with cursor() as cur:
        cur.execute(
            "INSERT INTO funnel_event (id, session_id, vendor_id, skill_id, event, detail_json,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (new_id("evt"), session_id, vendor_id, skill_id, event, dumps(detail or {}), now()),
        )


def funnel_summary(vendor_id: str) -> dict:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT event, COUNT(*) AS n FROM funnel_event WHERE vendor_id=? GROUP BY event",
            (vendor_id,),
        ).fetchall()
        sessions = cur.execute(
            "SELECT status, COUNT(*) AS n FROM session WHERE vendor_id=? GROUP BY status",
            (vendor_id,),
        ).fetchall()
    return {
        "events": {r["event"]: r["n"] for r in rows},
        "sessions": {r["status"]: r["n"] for r in sessions},
    }


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

def start_session(*, vendor_slug: str, skill_slug: str, email: Optional[str] = None,
                  terminal: Optional[str] = None) -> dict:
    """Begin a run. Only published skills are runnable."""
    skill = get_skill_by_slug(vendor_slug, skill_slug)
    if not skill:
        raise KeyError(f"no skill {skill_slug!r} for vendor {vendor_slug!r}")
    if skill["status"] != "published":
        raise PermissionError(
            "This skill is a draft. Drafts are invisible to customers and agents until published."
        )

    # Linked, so the vendor knows who is in front of them — but the session does
    # not inherit the profile's facts. Nobody proved they own this address, and
    # an inherited fact is one that leaves again through the package. A session
    # started under a stranger's email is therefore indistinguishable from one
    # started under a new one, which is the point: the difference was the oracle.
    profile = upsert_profile(email) if email else None
    sid = new_id("ses")
    status = {s["id"]: PENDING for s in skill["sections"]}
    if skill["sections"]:
        status[skill["sections"][0]["id"]] = ACTIVE

    with cursor() as cur:
        cur.execute(
            "INSERT INTO session (id, vendor_id, skill_id, skill_version, profile_id,"
            " section_status_json, facts_json, status, terminal, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,'active',?,?,?)",
            (sid, skill["vendor_id"], skill["id"], skill["version"],
             profile["id"] if profile else None, dumps(status),
             dumps({}), terminal, now(), now()),
        )
    record_event("session_started", session_id=sid, vendor_id=skill["vendor_id"],
                 skill_id=skill["id"], detail={"terminal": terminal})
    return get_session(sid)


def get_session(session_id: str) -> Optional[dict]:
    with cursor() as cur:
        row = cur.execute("SELECT * FROM session WHERE id=?", (session_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["section_status"] = loads(d.pop("section_status_json"), {})
    d["facts"] = loads(d.pop("facts_json"), {})
    return d


def _skill_for(session: dict) -> dict:
    skill = get_skill(session["skill_id"])
    if not skill:
        raise KeyError("the skill behind this session no longer exists")
    return skill


def _sync_status(session: dict, skill: dict) -> dict:
    """Reconcile status against the skill's current sections.

    A vendor may have added or removed a section since this session started.
    New sections join as pending; statuses for deleted sections are dropped.
    """
    status = dict(session["section_status"])
    ids = {s["id"] for s in skill["sections"]}
    for sid in list(status):
        if sid not in ids:
            del status[sid]
    for s in skill["sections"]:
        status.setdefault(s["id"], PENDING)
    if status and ACTIVE not in status.values() and any(v == PENDING for v in status.values()):
        for s in skill["sections"]:
            if status[s["id"]] == PENDING:
                status[s["id"]] = ACTIVE
                break
    return status


def _save_status(session_id: str, status: dict, *, complete: bool = False) -> None:
    with cursor() as cur:
        cur.execute(
            "UPDATE session SET section_status_json=?, status=?, updated_at=? WHERE id=?",
            (dumps(status), "complete" if complete else "active", now(), session_id),
        )


def current_section(session_id: str) -> Optional[dict]:
    session = get_session(session_id)
    if not session:
        raise KeyError(session_id)
    skill = _skill_for(session)
    status = _sync_status(session, skill)
    for i, s in enumerate(skill["sections"]):
        if status.get(s["id"]) == ACTIVE:
            return _present(s, i, skill, session)
    return None


def _present(section: dict, index: int, skill: dict, session: dict) -> dict:
    """Render a section for the customer's AI, including where it goes next."""
    vendor = get_vendor(skill["vendor_id"])
    total = len(skill["sections"])
    nxt = skill["sections"][index + 1] if index + 1 < total else None
    return {
        "section_id": section["id"],
        "position": index + 1,
        "of": total,
        "name": section["name"],
        "says": section["says"],
        "asks": section["asks"],
        "apps": section["apps"],
        "collects": section["collects"],
        "next": {"name": nxt["name"], "position": index + 2} if nxt else None,
        "is_last": nxt is None,
        "on_finish": destination_label(skill["destination"], vendor["name"]) if nxt is None else None,
    }


def complete_section(session_id: str, section_id: str,
                     facts: Optional[dict] = None) -> dict:
    """Mark a section done and hand back the next one.

    This is `get_next_section` from the brief, with the completion folded in so a
    caller cannot record progress and then forget to advance.
    """
    session = get_session(session_id)
    if not session:
        raise KeyError(session_id)
    skill = _skill_for(session)
    status = _sync_status(session, skill)
    if section_id not in status:
        raise KeyError(f"section {section_id!r} is not part of this skill")

    status[section_id] = COMPLETE
    if facts:
        merged = dict(session["facts"])
        merged.update(facts)
        with cursor() as cur:
            cur.execute("UPDATE session SET facts_json=? WHERE id=?", (dumps(merged), session_id))
        # Facts used to flow back to the portable profile here, so the customer's
        # next skill at any vendor started further along. It also meant a caller
        # who typed someone else's email could overwrite that person's record, so
        # the write waits on #15 along with the read. The session keeps its own
        # facts either way, so this run is unaffected.

    nxt_index, nxt = None, None
    for i, s in enumerate(skill["sections"]):
        if status.get(s["id"]) == PENDING:
            nxt_index, nxt = i, s
            break

    if nxt:
        status[nxt["id"]] = ACTIVE
        _save_status(session_id, status)
        record_event("section_completed", session_id=session_id, vendor_id=skill["vendor_id"],
                     skill_id=skill["id"], detail={"section": section_id})
        return {"done": False, "section": _present(nxt, nxt_index, skill, session)}

    _save_status(session_id, status, complete=True)
    record_event("skill_completed", session_id=session_id, vendor_id=skill["vendor_id"],
                 skill_id=skill["id"])
    return {"done": True, "section": None}


def progress(session_id: str) -> dict:
    session = get_session(session_id)
    if not session:
        raise KeyError(session_id)
    skill = _skill_for(session)
    status = _sync_status(session, skill)
    total = len(skill["sections"])
    done = sum(1 for v in status.values() if v == COMPLETE)
    return {
        "session_id": session_id,
        "skill": skill["name"],
        "status": session["status"],
        "completed": done,
        "total": total,
        "fraction": round(done / total, 3) if total else 0.0,
        "sections": [
            {"name": s["name"], "status": status.get(s["id"], PENDING)} for s in skill["sections"]
        ],
    }


def validate_package(session_id: str) -> dict:
    """Gaps and the next question — the doc-checklist app's engine.

    This is the `lenni_validate_package` behavior the brief says to keep. It
    reports what is still missing rather than judging the deal, because judging
    the deal is the vendor's job and never ours.
    """
    session = get_session(session_id)
    if not session:
        raise KeyError(session_id)
    skill = _skill_for(session)
    status = _sync_status(session, skill)
    gaps = []
    for s in skill["sections"]:
        if status.get(s["id"]) != COMPLETE:
            gaps.append({
                "section": s["name"],
                "needs": s["collects"] or ["section not completed"],
            })
    return {
        "complete": not gaps,
        "gap_count": len(gaps),
        "gaps": gaps,
        "next_question": _next_question(skill, status),
    }


def _next_question(skill: dict, status: dict) -> Optional[str]:
    for s in skill["sections"]:
        if status.get(s["id"]) != COMPLETE and s["asks"]:
            return s["asks"][0]
    return None


def finish(session_id: str) -> dict:
    """Terminal step: assemble outputs and hand back the closing message."""
    from .destination import assemble  # local import keeps the cycle out of module load

    session = get_session(session_id)
    if not session:
        raise KeyError(session_id)
    skill = _skill_for(session)
    vendor = get_vendor(skill["vendor_id"])
    check = validate_package(session_id)
    outputs = assemble(session_id)
    fp = compile_final_prompt(dest=skill["destination"], vendor_name=vendor["name"])
    record_event("delivery", session_id=session_id, vendor_id=skill["vendor_id"],
                 skill_id=skill["id"], detail={"mode": skill["destination"]["mode"]})
    return {
        "validated": check["complete"],
        "gaps": check["gaps"],
        "outputs": outputs,
        "destination": destination_label(skill["destination"], vendor["name"]),
        "closing_message": fp.text(),
    }


def list_sessions(vendor_id: str, limit: int = 50) -> list[dict]:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT s.*, k.name AS skill_name, c.email AS email FROM session s"
            " LEFT JOIN vendor_skill k ON k.id = s.skill_id"
            " LEFT JOIN customer_profile c ON c.id = s.profile_id"
            " WHERE s.vendor_id=? ORDER BY s.created_at DESC LIMIT ?",
            (vendor_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["section_status"] = loads(d.pop("section_status_json"), {})
        d.pop("facts_json", None)
        out.append(d)
    return out
