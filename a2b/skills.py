"""Vendors, vendor skills, sections, publishing.

The load-bearing rule in this module is pinning. `seed_skill_from_template`
takes a full copy of the template's sections at version T and records
`seeded_from_template_version`. Nothing in this file ever re-reads the template
to refresh a vendor's skill. When Lenni ships T+1 the vendor's rows do not
move, and the only path to the new content is `adopt_template_version`, which a
vendor has to call deliberately.

Getting this wrong is the single most damaging bug available in this codebase:
it would rewrite, without asking, the words a bank has approved for talking to
its own customers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import registry
from .compiler import (
    DEFAULT_DESTINATION,
    DEFAULT_ENTRY_MODE,
    ENTRY_MODES,
    ENTRY_TEXT_KEYS,
    can_publish,
    compile_entry_prompt,
    normalize_destination,
)
from .db import cursor, dumps, loads, new_id, now
from .links import hosted_start_url, section_url, skill_doc_url


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "skill"


# --------------------------------------------------------------------------
# Vendors
# --------------------------------------------------------------------------

def create_vendor(*, name: str, industry_key: str, website: str = "",
                  description: str = "", slug: Optional[str] = None) -> str:
    """Register a vendor.

    The slug is deduplicated rather than enforced as globally unique on the
    name. Two banks called "First National" is an ordinary situation, and
    failing the signup over it would be absurd.
    """
    vid = new_id("ven")
    with cursor() as cur:
        base = slug or slugify(name)
        candidate, n = base, 2
        while cur.execute("SELECT 1 FROM vendor WHERE slug=?", (candidate,)).fetchone():
            candidate = f"{base}-{n}"
            n += 1
        cur.execute(
            "INSERT INTO vendor (id, slug, name, industry_key, website, description, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (vid, candidate, name, industry_key, website, description, now()),
        )
    return vid


def get_vendor(vendor_id: str) -> Optional[dict]:
    with cursor() as cur:
        row = cur.execute("SELECT * FROM vendor WHERE id=?", (vendor_id,)).fetchone()
    return dict(row) if row else None


def get_vendor_by_slug(slug: str) -> Optional[dict]:
    with cursor() as cur:
        row = cur.execute("SELECT * FROM vendor WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def list_vendors() -> list[dict]:
    with cursor() as cur:
        rows = cur.execute("SELECT * FROM vendor ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def set_convey_url(vendor_id: str, url: str) -> None:
    with cursor() as cur:
        cur.execute("UPDATE vendor SET convey_url=? WHERE id=?", (url, vendor_id))


def add_member(vendor_id: str, email: str, role: str = "editor", status: str = "invited") -> str:
    mid = new_id("mem")
    with cursor() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO vendor_member (id, vendor_id, email, role, status, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (mid, vendor_id, email.lower(), role, status, now()),
        )
    return mid


def remove_member(vendor_id: str, email: str) -> None:
    with cursor() as cur:
        cur.execute(
            "DELETE FROM vendor_member WHERE vendor_id=? AND email=?",
            (vendor_id, email.lower()),
        )


def list_members(vendor_id: str) -> list[dict]:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM vendor_member WHERE vendor_id=? ORDER BY created_at", (vendor_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Skills
# --------------------------------------------------------------------------

def _unique_slug(cur, vendor_id: str, base: str) -> str:
    slug, n = base, 2
    while cur.execute(
        "SELECT 1 FROM vendor_skill WHERE vendor_id=? AND slug=?", (vendor_id, slug)
    ).fetchone():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _insert_sections(cur, skill_id: str, specs) -> None:
    for i, spec in enumerate(specs):
        cur.execute(
            "INSERT INTO skill_section (id, skill_id, position, name, summary, says,"
            " asks_json, collects_json, apps_json, is_core, origin_industry, edited)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
            (
                new_id("sec"), skill_id, i, spec.name, spec.summary, spec.says,
                dumps(list(spec.asks)), dumps(list(spec.collects)), dumps(list(spec.apps)),
                1 if spec.is_core else 0, spec.origin_industry,
            ),
        )


def seed_skill_from_template(vendor_id: str, template_id: str,
                             name: Optional[str] = None) -> str:
    """Copy a template at its CURRENT version into a vendor skill, and pin it.

    The copy is deep and total — every section, question and app becomes a row
    the vendor owns. After this returns, the template is no longer consulted for
    this skill's content.
    """
    tmpl = registry.template(template_id)
    vendor = get_vendor(vendor_id)
    if not vendor:
        raise KeyError(f"unknown vendor {vendor_id!r}")

    skill_id = new_id("skl")
    display_name = name or tmpl.name
    base_slug = slugify(re.sub(r"^uw-", "", tmpl.product_key or display_name))

    with cursor() as cur:
        slug = _unique_slug(cur, vendor_id, base_slug)
        cur.execute(
            "INSERT INTO vendor_skill (id, vendor_id, slug, name, product_key,"
            " seeded_from_template_id, seeded_from_template_version, is_custom, status,"
            " version, entry_prompt, entry_mode, destination_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,0,'draft',1,'',?,?,?,?)",
            (
                skill_id, vendor_id, slug, display_name, tmpl.product_key,
                tmpl.id, tmpl.version, DEFAULT_ENTRY_MODE,
                dumps(DEFAULT_DESTINATION), now(), now(),
            ),
        )
        _insert_sections(cur, skill_id, tmpl.sections)

    recompile(skill_id)
    return skill_id


def create_custom_skill(vendor_id: str, name: str) -> str:
    """A skill with no template behind it. Unmatched ingest offerings land here."""
    skill_id = new_id("skl")
    with cursor() as cur:
        slug = _unique_slug(cur, vendor_id, slugify(name))
        cur.execute(
            "INSERT INTO vendor_skill (id, vendor_id, slug, name, product_key,"
            " seeded_from_template_id, seeded_from_template_version, is_custom, status,"
            " version, entry_prompt, entry_mode, destination_json, created_at, updated_at)"
            " VALUES (?,?,?,?,NULL,NULL,NULL,1,'draft',1,'',?,?,?,?)",
            (skill_id, vendor_id, slug, name, DEFAULT_ENTRY_MODE,
             dumps(DEFAULT_DESTINATION), now(), now()),
        )
        cur.execute(
            "INSERT INTO skill_section (id, skill_id, position, name, summary, says,"
            " asks_json, collects_json, apps_json, is_core, origin_industry, edited)"
            " VALUES (?,?,0,?,?,'','[]','[]','[]',0,NULL,0)",
            (new_id("sec"), skill_id, "Getting to know you",
             "Custom section — draft it, or add one from the Library"),
        )
    recompile(skill_id)
    return skill_id


def get_skill(skill_id: str) -> Optional[dict]:
    with cursor() as cur:
        row = cur.execute("SELECT * FROM vendor_skill WHERE id=?", (skill_id,)).fetchone()
        if not row:
            return None
        secs = cur.execute(
            "SELECT * FROM skill_section WHERE skill_id=? ORDER BY position", (skill_id,)
        ).fetchall()
    return _hydrate(dict(row), [dict(s) for s in secs])


def get_skill_by_slug(vendor_slug: str, skill_slug: str) -> Optional[dict]:
    with cursor() as cur:
        row = cur.execute(
            "SELECT s.* FROM vendor_skill s JOIN vendor v ON v.id=s.vendor_id"
            " WHERE v.slug=? AND s.slug=?", (vendor_slug, skill_slug),
        ).fetchone()
        if not row:
            return None
        secs = cur.execute(
            "SELECT * FROM skill_section WHERE skill_id=? ORDER BY position", (row["id"],)
        ).fetchall()
    return _hydrate(dict(row), [dict(s) for s in secs])


def _hydrate(row: dict, secs: list[dict]) -> dict:
    row["destination"] = normalize_destination(loads(row.pop("destination_json"), {}))
    row["is_custom"] = bool(row["is_custom"])
    # Whatever paragraphs of the entry prompt this vendor has rewritten. Absent
    # on a row read before the column existed, and empty for a skill nobody has
    # edited — both mean "use ours".
    row["prompt_texts"] = loads(row.pop("prompt_texts_json", None), {}) or {}
    # Absent on a row read before the column was added.
    row["entry_mode"] = row.get("entry_mode") or "staged"
    # Legacy view of the same setting, kept so older callers keep working.
    row["connector_only"] = row["entry_mode"] == "connector"
    row["sections"] = [
        {
            "id": s["id"],
            "position": s["position"],
            "name": s["name"],
            "summary": s["summary"] or "",
            "says": s["says"] or "",
            "asks": loads(s["asks_json"], []),
            "collects": loads(s["collects_json"], []),
            "apps": loads(s["apps_json"], []),
            "is_core": bool(s["is_core"]),
            "origin_industry": s["origin_industry"],
            "edited": bool(s["edited"]),
        }
        for s in secs
    ]
    tid = row.get("seeded_from_template_id")
    pinned = row.get("seeded_from_template_version")
    available = registry.current_version(tid) if tid else None
    row["template_status"] = {
        "template_id": tid,
        "pinned_version": pinned,
        "available_version": available,
        # Surfaced in the UI as "pinned v1.0 · v1.1 available". Never acted on
        # automatically — rule 1.
        "update_available": bool(tid and available and available != pinned),
    }
    return row


def list_skills(vendor_id: str) -> list[dict]:
    with cursor() as cur:
        rows = cur.execute(
            "SELECT * FROM vendor_skill WHERE vendor_id=? ORDER BY created_at", (vendor_id,)
        ).fetchall()
        out = []
        for r in rows:
            secs = cur.execute(
                "SELECT * FROM skill_section WHERE skill_id=? ORDER BY position", (r["id"],)
            ).fetchall()
            out.append(_hydrate(dict(r), [dict(s) for s in secs]))
    return out


def _touch(cur, skill_id: str) -> None:
    cur.execute("UPDATE vendor_skill SET updated_at=? WHERE id=?", (now(), skill_id))


def recompile(skill_id: str) -> str:
    """Regenerate the entry prompt from current config. Call after every change.

    The prompt carries the first section's wording verbatim, so this has to run
    after any edit to `says`, `asks` or `collects` as well as after a rename or a
    reorder — and a reorder is the sharp one, because it changes which section is
    the one the customer is handed.

    It also carries the closing message now, which puts the destination on the
    same footing: changing where packages go, or the text that announces it, is
    a prompt change. `set_destination` and `connect_convey` both land here.
    """
    skill = get_skill(skill_id)
    if not skill:
        raise KeyError(skill_id)
    vendor = get_vendor(skill["vendor_id"])
    prompt = compile_entry_prompt(
        vendor_name=vendor["name"],
        vendor_slug=vendor["slug"],
        skill_name=skill["name"],
        skill_slug=skill["slug"],
        sections=skill["sections"],
        next_url=section_url(vendor["slug"], skill["slug"], 2),
        workflow_url=hosted_start_url(vendor["slug"], skill["slug"], fmt="md"),
        mode=skill["entry_mode"],
        guide_url=skill_doc_url(vendor["slug"], skill["slug"], "context.md"),
        card_url=skill_doc_url(vendor["slug"], skill["slug"], "llms.txt"),
        dest=skill["destination"],
        texts=skill["prompt_texts"],
    )
    with cursor() as cur:
        cur.execute("UPDATE vendor_skill SET entry_prompt=?, updated_at=? WHERE id=?",
                    (prompt, now(), skill_id))
    return prompt


def set_prompt_texts(skill_id: str, texts: dict) -> dict:
    """Replace some of the entry prompt's wording with the vendor's own.

    Merges rather than replaces, so saving one paragraph does not silently reset
    the others, and a key set to None or "" is how a vendor puts one paragraph
    back to ours without resetting the lot.

    Only keys in `ENTRY_TEXT_KEYS` are accepted. Anything else is dropped rather
    than stored: the prompt is a compiled artefact and this is the seam we
    deliberately left in it, so an unknown key here means either a bug or
    somebody probing, and neither should be able to put text in front of a
    customer.
    """
    skill = get_skill(skill_id)
    if not skill:
        raise KeyError(skill_id)

    merged = dict(skill["prompt_texts"])
    for key, value in (texts or {}).items():
        if key not in ENTRY_TEXT_KEYS:
            continue
        cleaned = (value or "").strip()
        if cleaned:
            merged[key] = cleaned
        else:
            merged.pop(key, None)

    with cursor() as cur:
        cur.execute("UPDATE vendor_skill SET prompt_texts_json=?, updated_at=? WHERE id=?",
                    (dumps(merged), now(), skill_id))
    recompile(skill_id)
    return merged


def set_entry_mode(skill_id: str, mode: str) -> str:
    """Choose what this skill's start link actually carries.

    `inline` carries the whole intake in the link and asks the customer's AI to
    fetch nothing, which is the only one of the four that behaves identically in
    every assistant. `doc` points at the published guide and needs nothing
    installed, but depends on that guide actually being fetched. `staged` carries
    section 1 in the link and chains the rest. `connector` carries nothing at all
    and refuses without the tools, which is the strongest and the least
    demonstrable.

    The trade is the vendor's to make per skill — a bank running a pilot with
    known users can afford `connector`; the same bank's public product page
    cannot — which is why this is stored rather than a deployment-wide constant.
    """
    if mode not in ENTRY_MODES:
        raise ValueError(f"unknown entry mode {mode!r}; expected one of {ENTRY_MODES}")
    with cursor() as cur:
        if not cur.execute("SELECT 1 FROM vendor_skill WHERE id=?", (skill_id,)).fetchone():
            raise KeyError(skill_id)
        cur.execute(
            "UPDATE vendor_skill SET entry_mode=?, connector_only=?, updated_at=? WHERE id=?",
            (mode, 1 if mode == "connector" else 0, now(), skill_id))
    return recompile(skill_id)


def recompile_all() -> int:
    """Rebuild every entry prompt. Runs at startup.

    Not only a one-off migration for skills compiled by an older version. A
    prompt embeds this deployment's public hostname, and on a quick tunnel that
    hostname changes every time the tunnel restarts — so a skill published last
    week would otherwise keep handing customers an address that no longer
    resolves. Cheap and idempotent: recompiling is pure function of stored config.
    """
    with cursor() as cur:
        ids = [r["id"] for r in cur.execute("SELECT id FROM vendor_skill").fetchall()]
    for skill_id in ids:
        recompile(skill_id)
    return len(ids)


# --------------------------------------------------------------------------
# Section editing
# --------------------------------------------------------------------------

def update_section(section_id: str, *, name: Optional[str] = None, says: Optional[str] = None,
                   asks: Optional[list[str]] = None, summary: Optional[str] = None,
                   apps: Optional[list[str]] = None) -> None:
    with cursor() as cur:
        row = cur.execute("SELECT skill_id FROM skill_section WHERE id=?", (section_id,)).fetchone()
        if not row:
            raise KeyError(section_id)
        sets, vals = ["edited=1"], []
        if name is not None:
            sets.append("name=?"); vals.append(name)
        if summary is not None:
            sets.append("summary=?"); vals.append(summary)
        if says is not None:
            sets.append("says=?"); vals.append(says)
        if asks is not None:
            sets.append("asks_json=?"); vals.append(dumps([a for a in asks if a.strip()]))
        if apps is not None:
            sets.append("apps_json=?"); vals.append(dumps(apps))
        vals.append(section_id)
        cur.execute(f"UPDATE skill_section SET {', '.join(sets)} WHERE id=?", vals)
        skill_id = row["skill_id"]
        _touch(cur, skill_id)
    recompile(skill_id)


def add_section(skill_id: str, spec: registry.SectionSpec) -> str:
    sid = new_id("sec")
    with cursor() as cur:
        n = cur.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS n FROM skill_section WHERE skill_id=?",
            (skill_id,),
        ).fetchone()["n"]
        cur.execute(
            "INSERT INTO skill_section (id, skill_id, position, name, summary, says,"
            " asks_json, collects_json, apps_json, is_core, origin_industry, edited)"
            " VALUES (?,?,?,?,?,?,?,?,?,0,?,0)",
            (sid, skill_id, n, spec.name, spec.summary, spec.says, dumps(list(spec.asks)),
             dumps(list(spec.collects)), dumps(list(spec.apps)), spec.origin_industry),
        )
        _touch(cur, skill_id)
    recompile(skill_id)
    return sid


def add_library_section(skill_id: str, industry_key: str, index: int) -> str:
    """Add a pre-drafted Library section, tagging it when it crosses industries."""
    items = registry.library().get(industry_key) or []
    if index < 0 or index >= len(items):
        raise IndexError(f"no library section {index} in {industry_key!r}")
    skill = get_skill(skill_id)
    vendor = get_vendor(skill["vendor_id"])
    spec = items[index].copy()
    # Only tag it when it actually came from somewhere else. A bank adding a
    # banking section should not see a "from Banking" badge on its own skill.
    spec.origin_industry = industry_key if industry_key != vendor["industry_key"] else None
    return add_section(skill_id, spec)


def add_blank_section(skill_id: str, name: str = "New section") -> str:
    return add_section(skill_id, registry.SectionSpec(name=name, summary="Draft it with Edit"))


def delete_section(section_id: str) -> None:
    """Core sections anchor the industry's flow and cannot be removed."""
    with cursor() as cur:
        row = cur.execute(
            "SELECT skill_id, is_core, name FROM skill_section WHERE id=?", (section_id,)
        ).fetchone()
        if not row:
            raise KeyError(section_id)
        if row["is_core"]:
            raise ValueError(
                f"{row['name']} is a core section — it can be edited, but not removed."
            )
        skill_id = row["skill_id"]
        cur.execute("DELETE FROM skill_section WHERE id=?", (section_id,))
        _renumber(cur, skill_id)
        _touch(cur, skill_id)
    recompile(skill_id)


def move_section(section_id: str, delta: int) -> None:
    with cursor() as cur:
        row = cur.execute(
            "SELECT skill_id, position FROM skill_section WHERE id=?", (section_id,)
        ).fetchone()
        if not row:
            raise KeyError(section_id)
        skill_id, pos = row["skill_id"], row["position"]
        target = cur.execute(
            "SELECT id, position FROM skill_section WHERE skill_id=? AND position=?",
            (skill_id, pos + delta),
        ).fetchone()
        if not target:
            return
        cur.execute("UPDATE skill_section SET position=? WHERE id=?", (target["position"], section_id))
        cur.execute("UPDATE skill_section SET position=? WHERE id=?", (pos, target["id"]))
        _touch(cur, skill_id)
    # The entry prompt embeds section order, so a move has to recompile it.
    recompile(skill_id)


def _renumber(cur, skill_id: str) -> None:
    rows = cur.execute(
        "SELECT id FROM skill_section WHERE skill_id=? ORDER BY position", (skill_id,)
    ).fetchall()
    for i, r in enumerate(rows):
        cur.execute("UPDATE skill_section SET position=? WHERE id=?", (i, r["id"]))


def add_app(section_id: str, app_id: str) -> None:
    if app_id not in registry.apps():
        raise KeyError(f"unknown app {app_id!r}")
    with cursor() as cur:
        row = cur.execute(
            "SELECT skill_id, apps_json FROM skill_section WHERE id=?", (section_id,)
        ).fetchone()
        if not row:
            raise KeyError(section_id)
        apps = loads(row["apps_json"], [])
        if app_id not in apps:
            apps.append(app_id)
        cur.execute("UPDATE skill_section SET apps_json=? WHERE id=?", (dumps(apps), section_id))
        _touch(cur, row["skill_id"])


def remove_app(section_id: str, app_id: str) -> None:
    with cursor() as cur:
        row = cur.execute(
            "SELECT skill_id, apps_json FROM skill_section WHERE id=?", (section_id,)
        ).fetchone()
        if not row:
            raise KeyError(section_id)
        apps = [a for a in loads(row["apps_json"], []) if a != app_id]
        cur.execute("UPDATE skill_section SET apps_json=? WHERE id=?", (dumps(apps), section_id))
        _touch(cur, row["skill_id"])


# --------------------------------------------------------------------------
# Destination and publishing
# --------------------------------------------------------------------------

def set_destination(skill_id: str, dest: dict) -> dict:
    """Change where finished packages go, and what the customer is told about it.

    Recompiles, because the closing message now travels inside the entry prompt.
    Without this, a vendor who switches from email to Convey — or pastes their
    secure link an hour after publishing — keeps handing out a link that tells
    customers to use the old one, and it would keep working, quietly, for as long
    as nobody edited a section.
    """
    merged = normalize_destination(dest)
    with cursor() as cur:
        cur.execute("UPDATE vendor_skill SET destination_json=?, updated_at=? WHERE id=?",
                    (dumps(merged), now(), skill_id))
    recompile(skill_id)
    return merged


def connect_convey(skill_id: str, url: str) -> dict:
    skill = get_skill(skill_id)
    if not skill:
        raise KeyError(skill_id)
    dest = dict(skill["destination"])
    dest["mode"] = "convey"
    dest["connected"] = True
    dest["link"] = url
    set_convey_url(skill["vendor_id"], url)
    # Convey becomes the terminal step, so the last section gets the connector app.
    if skill["sections"]:
        last = skill["sections"][-1]
        if "convey-link" not in last["apps"]:
            add_app(last["id"], "convey-link")
    return set_destination(skill_id, dest)


def publish_skill(skill_id: str) -> dict:
    """Publishing is the consent moment: it makes a skill visible to customers
    and agents, and it regenerates the vendor's manifest.

    A skill with nowhere to deliver is refused. In the demo you can publish one
    and it looks fine, which would strand a real customer at the last step.
    """
    skill = get_skill(skill_id)
    if not skill:
        raise KeyError(skill_id)
    ok, blockers = can_publish(skill["destination"])
    if not ok:
        raise ValueError("; ".join(blockers))
    with cursor() as cur:
        cur.execute(
            "UPDATE vendor_skill SET status='published', published_at=?, updated_at=?,"
            " version=version+CASE WHEN status='published' THEN 1 ELSE 0 END WHERE id=?",
            (now(), now(), skill_id),
        )
    recompile(skill_id)
    return get_skill(skill_id)


def unpublish_skill(skill_id: str) -> dict:
    with cursor() as cur:
        cur.execute("UPDATE vendor_skill SET status='draft', updated_at=? WHERE id=?",
                    (now(), skill_id))
    return get_skill(skill_id)


def rename_skill(skill_id: str, name: str) -> None:
    with cursor() as cur:
        cur.execute("UPDATE vendor_skill SET name=?, updated_at=? WHERE id=?", (name, now(), skill_id))
    recompile(skill_id)


def delete_skill(skill_id: str) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM vendor_skill WHERE id=?", (skill_id,))


# --------------------------------------------------------------------------
# Template updates — vendor-initiated only
# --------------------------------------------------------------------------

def template_diff(skill_id: str) -> dict:
    """What would change if this skill adopted the current template version.

    Shown before anything is applied. Sections the vendor has edited are called
    out separately, because those are the ones where adopting costs them work.
    """
    skill = get_skill(skill_id)
    if not skill:
        raise KeyError(skill_id)
    status = skill["template_status"]
    if not status["update_available"]:
        return {"update_available": False, "changes": [], "edited_sections": []}

    tmpl = registry.template(status["template_id"])
    current_names = [s["name"] for s in skill["sections"]]
    new_names = [s.name for s in tmpl.sections]
    changes = []
    for n in new_names:
        if n not in current_names:
            changes.append({"type": "added", "section": n})
    for n in current_names:
        if n not in new_names:
            changes.append({"type": "removed_upstream", "section": n})
    return {
        "update_available": True,
        "from_version": status["pinned_version"],
        "to_version": status["available_version"],
        "changes": changes,
        "edited_sections": [s["name"] for s in skill["sections"] if s["edited"]],
        "changelog": [c for c in tmpl.changelog if str(c.get("version")) == tmpl.version],
    }


def adopt_template_version(skill_id: str, *, keep_added_sections: bool = True) -> dict:
    """Re-seed from the current template version. Destructive and explicit.

    Only ever called because a vendor asked for it. Their edits to core sections
    are replaced; sections they added themselves survive by default.
    """
    skill = get_skill(skill_id)
    if not skill:
        raise KeyError(skill_id)
    tid = skill["template_status"]["template_id"]
    if not tid:
        raise ValueError("This skill was not seeded from a template.")
    tmpl = registry.template(tid)

    added = [s for s in skill["sections"] if not s["is_core"]] if keep_added_sections else []
    with cursor() as cur:
        cur.execute("DELETE FROM skill_section WHERE skill_id=?", (skill_id,))
        _insert_sections(cur, skill_id, tmpl.sections)
        n = len(tmpl.sections)
        for i, s in enumerate(added):
            cur.execute(
                "INSERT INTO skill_section (id, skill_id, position, name, summary, says,"
                " asks_json, collects_json, apps_json, is_core, origin_industry, edited)"
                " VALUES (?,?,?,?,?,?,?,?,?,0,?,?)",
                (new_id("sec"), skill_id, n + i, s["name"], s["summary"], s["says"],
                 dumps(s["asks"]), dumps(s["collects"]), dumps(s["apps"]),
                 s["origin_industry"], 1 if s["edited"] else 0),
            )
        cur.execute(
            "UPDATE vendor_skill SET seeded_from_template_version=?, updated_at=? WHERE id=?",
            (tmpl.version, now(), skill_id),
        )
    recompile(skill_id)
    return get_skill(skill_id)
