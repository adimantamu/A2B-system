"""The exit: assemble what the customer produced, then route it to the vendor.

Secure delivery is the terminal step of every skill. Convey is the included
default, not a requirement — a vendor can point at their own secure link or, with
an honest warning attached, at an email address.

The outputs are a recap of the whole session, not a credit memo. Nothing here
scores, prices or decides anything.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .config import PACKAGE_DIR, ensure_dirs
from .compiler import normalize_destination
from .db import cursor


def _gather(session_id: str) -> dict:
    from .sessions import get_session, progress
    from .skills import get_skill, get_vendor

    session = get_session(session_id)
    if not session:
        raise KeyError(session_id)
    skill = get_skill(session["skill_id"])
    vendor = get_vendor(skill["vendor_id"])
    return {
        "session": session,
        "skill": skill,
        "vendor": vendor,
        "progress": progress(session_id),
    }


def build_markdown(session_id: str) -> str:
    """Structured MD of every section — loads into any system or AI."""
    ctx = _gather(session_id)
    skill, vendor, session = ctx["skill"], ctx["vendor"], ctx["session"]
    prog = {s["name"]: s["status"] for s in ctx["progress"]["sections"]}
    facts = session["facts"]

    out = [
        f"# {skill['name']} — package recap",
        "",
        f"**Prepared for:** {vendor['name']}  ",
        f"**Session:** `{session_id}`  ",
        f"**Completed:** {ctx['progress']['completed']} of {ctx['progress']['total']} sections",
        "",
        "> Preparation only. Not a credit decision, not a rate quote, not a commitment.",
        "",
    ]
    for s in skill["sections"]:
        out += [f"## {s['name']}", "", f"_{prog.get(s['name'], 'pending')}_", ""]
        if s["collects"]:
            out.append("**Collected:**")
            out += [f"- {c}" for c in s["collects"]]
            out.append("")
        answered = {k: v for k, v in facts.items() if k.startswith(s["name"].lower().replace(" ", "_"))}
        if answered:
            out.append("**Provided:**")
            out += [f"- **{k}**: {v}" for k, v in answered.items()]
            out.append("")
    if facts:
        out += ["## All captured facts", "", "```json", json.dumps(facts, indent=2), "```", ""]
    return "\n".join(out)


def build_doc_list(session_id: str) -> list[dict]:
    """The checklist with status — attached versus still owed."""
    ctx = _gather(session_id)
    skill = ctx["skill"]
    statuses = {s["name"]: s["status"] for s in ctx["progress"]["sections"]}
    docs = []
    for s in skill["sections"]:
        if "doc-checklist" not in s["apps"]:
            continue
        for item in s["collects"]:
            docs.append({
                "section": s["name"],
                "item": item,
                "status": "provided" if statuses.get(s["name"]) == "complete" else "outstanding",
            })
    return docs


def build_pdf(session_id: str, path: Path) -> bool:
    """Typeset the recap. Returns False when ReportLab is not installed.

    PDF generation is optional on purpose — the system stays fully functional
    without it, and a missing optional dependency should degrade the output
    rather than fail the customer's delivery at the last step.
    """
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        return False

    ctx = _gather(session_id)
    skill, vendor = ctx["skill"], ctx["vendor"]
    statuses = {s["name"]: s["status"] for s in ctx["progress"]["sections"]}

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=17, spaceAfter=6)
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=9, textColor="#5a6b80")
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceBefore=14, spaceAfter=4)

    doc = SimpleDocTemplate(
        str(path), pagesize=LETTER,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.85 * inch, bottomMargin=0.85 * inch,
        title=f"{skill['name']} — package recap",
    )
    flow: list[Any] = [
        Paragraph(f"{skill['name']} — package recap", h1),
        Paragraph(f"Prepared for {vendor['name']}", meta),
        Paragraph(
            f"{ctx['progress']['completed']} of {ctx['progress']['total']} sections completed", meta
        ),
        Spacer(1, 10),
        Paragraph(
            "<i>Preparation only. Not a credit decision, not a rate quote, and not a "
            "commitment to lend.</i>", meta,
        ),
    ]
    for s in skill["sections"]:
        flow.append(Paragraph(f"{s['name']} — {statuses.get(s['name'], 'pending')}", h2))
        if s["says"]:
            flow.append(Paragraph(s["says"], styles["Normal"]))
            flow.append(Spacer(1, 5))
        if s["collects"]:
            flow.append(ListFlowable(
                [ListItem(Paragraph(c, styles["Normal"])) for c in s["collects"]],
                bulletType="bullet", leftIndent=14,
            ))
    doc.build(flow)
    return True


def assemble(session_id: str) -> dict:
    """Produce whatever the destination says to produce."""
    ensure_dirs()
    ctx = _gather(session_id)
    dest = normalize_destination(ctx["skill"]["destination"])
    outputs: dict[str, Any] = {"pdf": None, "markdown": None, "doc_list": None, "notes": []}

    if dest["outputs"]["md"]:
        md_path = PACKAGE_DIR / f"{session_id}.md"
        md_path.write_text(build_markdown(session_id), encoding="utf-8")
        outputs["markdown"] = str(md_path)

    if dest["outputs"]["doclist"]:
        outputs["doc_list"] = build_doc_list(session_id)

    if dest["outputs"]["pdf"]:
        pdf_path = PACKAGE_DIR / f"{session_id}.pdf"
        if build_pdf(session_id, pdf_path):
            outputs["pdf"] = str(pdf_path)
        else:
            outputs["notes"].append(
                "PDF skipped — ReportLab is not installed. `pip install reportlab` to enable it."
            )
    return outputs


def delivery_instruction(skill: dict, vendor: dict) -> dict:
    """Where the package goes, and how honest we are being about it."""
    d = normalize_destination(skill["destination"])
    if d["mode"] == "convey":
        return {
            "mode": "convey",
            "target": d["link"] or vendor.get("convey_url") or "",
            "secure": True,
            "warning": None if d["connected"] else "Convey is not connected for this skill.",
        }
    if d["mode"] == "link":
        return {"mode": "link", "target": d["link"], "secure": True, "warning": None}
    return {
        "mode": "email",
        "target": d["email"],
        "secure": False,
        "warning": (
            "Email is not a secure channel. Summaries are fine; original documents such as "
            "tax returns should travel by a secure method."
        ),
    }
