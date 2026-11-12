"""Loads the content registry: industries, products, templates, library, apps.

This side of the model is read-only content on disk, not database rows. It is
Lenni-authored and ships with the code, which is what lets a template carry a
version and a changelog that a vendor's pinned copy can be compared against.
"""
from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

from .config import CONTENT_DIR, TEMPLATE_DIR


def _load(path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass(frozen=True)
class Industry:
    key: str
    name: str
    templates: bool
    blurb: str = ""
    signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class Product:
    key: str
    industry: str
    name: str
    tier: int
    template: Optional[str]
    site_phrases: tuple[str, ...]
    bespoke: bool = False
    """True when someone authored a template for this product specifically.

    False means `template` names the generic intake synthesised from the
    industry's core sections. Both are seedable; the distinction is what the
    roadmap is made of, so it stays visible rather than being flattened into
    "has a template".
    """


@dataclass
class SectionSpec:
    """A section as authored — not yet owned by any vendor."""
    name: str
    summary: str = ""
    says: str = ""
    asks: list[str] = field(default_factory=list)
    collects: list[str] = field(default_factory=list)
    apps: list[str] = field(default_factory=list)
    is_core: bool = False
    origin_industry: Optional[str] = None

    def copy(self) -> "SectionSpec":
        return SectionSpec(
            name=self.name,
            summary=self.summary,
            says=self.says,
            asks=list(self.asks),
            collects=list(self.collects),
            apps=list(self.apps),
            is_core=self.is_core,
            origin_industry=self.origin_industry,
        )


@dataclass(frozen=True)
class SkillTemplate:
    id: str
    version: str
    name: str
    product_key: Optional[str]
    industry: str
    sections: tuple[SectionSpec, ...]
    changelog: tuple[dict, ...]


@dataclass(frozen=True)
class App:
    id: str
    icon: str
    name: str
    tag: str
    wave: int
    blurb: str
    implemented: bool


def _norm(text: str) -> str:
    return " ".join((text or "").split())


@functools.lru_cache(maxsize=1)
def industries() -> dict[str, Industry]:
    raw = _load(CONTENT_DIR / "industries.yaml")
    out: dict[str, Industry] = {}
    for row in raw.get("industries", []):
        out[row["key"]] = Industry(
            key=row["key"],
            name=row["name"],
            templates=bool(row.get("templates")),
            blurb=row.get("blurb", ""),
            signals=tuple(row.get("signals", [])),
        )
    return out


@functools.lru_cache(maxsize=1)
def products() -> dict[str, Product]:
    """The catalogue, with each product resolved to the intake it can run.

    A product whose YAML names a template gets that template. A product whose
    YAML names none falls back to the generic intake for its industry, under a
    template id equal to its own key — see `templates()`, which synthesises it.

    The fallback only applies where the industry actually has core sections to
    build from. Without them there is nothing to run, so `template` stays None
    and the product is catalogued but not yet sellable: still matched by the
    scanner, still counted as demand, just not offerable to a customer.
    """
    raw = _load(CONTENT_DIR / "products.yaml")
    has_core = set(_core_sections())
    out: dict[str, Product] = {}
    for row in raw.get("products", []):
        authored = row.get("template")
        generic = row["key"] if row["industry"] in has_core else None
        out[row["key"]] = Product(
            key=row["key"],
            industry=row["industry"],
            name=row["name"],
            tier=int(row.get("tier", 3)),
            template=authored or generic,
            site_phrases=tuple(row.get("site_phrases", [])),
            bespoke=bool(authored),
        )
    return out


@functools.lru_cache(maxsize=1)
def apps() -> dict[str, App]:
    raw = _load(CONTENT_DIR / "apps.yaml")
    out: dict[str, App] = {}
    for row in raw.get("apps", []):
        out[row["id"]] = App(
            id=row["id"],
            icon=row.get("icon", ""),
            name=row["name"],
            tag=row.get("tag", ""),
            wave=int(row.get("wave", 1)),
            blurb=_norm(row.get("blurb", "")),
            implemented=bool(row.get("implemented")),
        )
    return out


@functools.lru_cache(maxsize=1)
def _core_sections() -> dict[str, list[SectionSpec]]:
    raw = _load(CONTENT_DIR / "core_sections.yaml")
    out: dict[str, list[SectionSpec]] = {}
    for industry_key, rows in raw.items():
        out[industry_key] = [
            SectionSpec(
                name=r["name"],
                summary=_norm(r.get("summary", "")),
                says=_norm(r.get("says", "")),
                asks=[_norm(a) for a in r.get("asks", [])],
                collects=list(r.get("collects", [])),
                apps=list(r.get("apps", [])),
                is_core=True,
            )
            for r in rows
        ]
    return out


def core_sections() -> dict[str, list[SectionSpec]]:
    """The industry intake, readable on its own.

    Templates are synthesised per product, so an industry whose services are not
    yet catalogued in products.yaml resolves to no templates at all — even though
    its intake is fully written here. This exposes that intake directly, which is
    what the public library shows for an industry still awaiting its service list.
    """
    return _core_sections()


@functools.lru_cache(maxsize=1)
def library() -> dict[str, list[SectionSpec]]:
    """Pre-drafted, addable sections, grouped by the industry that authored them."""
    raw = _load(CONTENT_DIR / "library_sections.yaml")
    out: dict[str, list[SectionSpec]] = {}
    for industry_key, rows in raw.items():
        out[industry_key] = [
            SectionSpec(
                name=r["name"],
                summary=_norm(r.get("summary", "")),
                says=_norm(r.get("says", "")),
                asks=[_norm(a) for a in r.get("asks", [])],
                collects=list(r.get("collects", [])),
                apps=list(r.get("apps", [])),
                is_core=False,
                origin_industry=industry_key,
            )
            for r in rows
        ]
    return out


def _apply_override(base: SectionSpec, ov: dict) -> SectionSpec:
    """Apply a template's tweak to a core section.

    Replacement keys (`name`, `asks`, …) overwrite. The `_append` variants add to
    what the core already says, which is the common case: a product usually needs
    two more questions on collateral, not a rewritten section.
    """
    s = base.copy()
    if "name" in ov:
        s.name = ov["name"]
    if "summary" in ov:
        s.summary = _norm(ov["summary"])
    if "says" in ov:
        s.says = _norm(ov["says"])
    if "asks" in ov:
        s.asks = [_norm(a) for a in ov["asks"]]
    if "collects" in ov:
        s.collects = list(ov["collects"])
    if "apps" in ov:
        s.apps = list(ov["apps"])
    for a in ov.get("asks_append", []):
        s.asks.append(_norm(a))
    for c in ov.get("collects_append", []):
        s.collects.append(c)
    for a in ov.get("apps_append", []):
        if a not in s.apps:
            s.apps.append(a)
    return s


def _resolve_sections(row: dict) -> list[SectionSpec]:
    core_key = row.get("core")
    sections: list[SectionSpec] = []
    if core_key:
        base = _core_sections().get(core_key)
        if base is None:
            raise KeyError(f"template {row['id']} references unknown core set {core_key!r}")
        overrides = row.get("overrides") or {}
        for spec in base:
            ov = overrides.get(spec.name)
            sections.append(_apply_override(spec, ov) if ov else spec.copy())
    for extra in row.get("extra_sections", []) or []:
        sections.append(
            SectionSpec(
                name=extra["name"],
                summary=_norm(extra.get("summary", "")),
                says=_norm(extra.get("says", "")),
                asks=[_norm(a) for a in extra.get("asks", [])],
                collects=list(extra.get("collects", [])),
                apps=list(extra.get("apps", [])),
                is_core=False,
            )
        )
    return sections


def _fingerprint(specs: list[SectionSpec]) -> str:
    """A short digest of section content, used as a generic intake's version.

    An authored template carries a version somebody typed, and the rule is to
    bump it on any content edit. A generic intake has no such line to edit — its
    content is the industry's core sections — so editing a core would otherwise
    change what new vendors seed while every pinned copy still claimed to be the
    same version. Deriving the version from the content closes that hole without
    asking whoever edits the core to remember anything.
    """
    blob = json.dumps(
        [[s.name, s.summary, s.says, s.asks, s.collects, s.apps] for s in specs],
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]


@functools.lru_cache(maxsize=1)
def templates() -> dict[str, SkillTemplate]:
    prods = products()
    out: dict[str, SkillTemplate] = {}
    for path in sorted(TEMPLATE_DIR.glob("*.yaml")):
        raw = _load(path)
        for row in raw.get("templates", []):
            product_key = row.get("product_key")
            industry = prods[product_key].industry if product_key in prods else row.get("industry", "")
            out[row["id"]] = SkillTemplate(
                id=row["id"],
                version=str(row["version"]),
                name=row["name"],
                product_key=product_key,
                industry=industry,
                sections=tuple(_resolve_sections(row)),
                changelog=tuple(row.get("changelog", [])),
            )

    # Every catalogued product whose industry has core sections becomes seedable,
    # running that industry's intake until someone writes it a tailored one. This
    # is what stops an industry being unsellable until all of its services are
    # authored: the generic intake asks the right questions in the right order
    # but knows nothing product-specific, which is a worse product than a
    # tailored template and a far better one than a dead button.
    #
    # Authored templates win — an id already loaded above is never replaced. When
    # a tailored template lands under the product's key, vendors seeded on the
    # generic version see the version change and are offered the update.
    for p in prods.values():
        if p.bespoke or not p.template or p.template in out:
            continue
        sections = [s.copy() for s in _core_sections()[p.industry]]
        out[p.template] = SkillTemplate(
            id=p.template,
            version=f"generic-{_fingerprint(sections)}",
            name=p.name,
            product_key=p.key,
            industry=p.industry,
            sections=tuple(sections),
            changelog=({
                "version": f"generic-{_fingerprint(sections)}",
                "note": f"The generic {p.industry} intake. Not yet tailored to "
                        f"{p.name}, so it asks nothing specific to it.",
            },),
        )
    return out


def template(template_id: str) -> SkillTemplate:
    try:
        return templates()[template_id]
    except KeyError as exc:
        raise KeyError(f"unknown skill template {template_id!r}") from exc


def products_for_industry(industry_key: str) -> list[Product]:
    return sorted(
        (p for p in products().values() if p.industry == industry_key),
        key=lambda p: (p.tier, p.name),
    )


def current_version(template_id: str) -> Optional[str]:
    t = templates().get(template_id)
    return t.version if t else None


def reload() -> None:
    """Drop caches. Used by tests and by the content-reload endpoint."""
    for fn in (industries, products, apps, _core_sections, library, templates):
        fn.cache_clear()
