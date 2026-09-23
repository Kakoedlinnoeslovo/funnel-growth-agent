"""Structured current landing. No media bytes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .config import Settings
from .models import MOVABLE_COMPONENTS, OMITTABLE_COMPONENTS

_yaml = YAML(typ="safe")

TEXT_KEYS = {
    "headline",
    "subhead",
    "ctaLabel",
    "reassurance",
    "layout",
    "stickyCta",
    "component",
    "pageTitle",
}

HERO_COMPONENTS = {"kittl-hero", "hero-carousel", "quiz-hero", "growth-block"}


def landing_image_targets(
    sections: list[dict[str, Any]],
) -> dict[str, tuple[str | None, list[tuple[Any, Any]]]]:
    """Named editable image slots, shared by analysis, generation and patching."""
    groups = {}
    for section, sid in zip(sections, resolve_section_ids(sections)):
        component = section.get("component")
        if component == "growth-block":
            images = section.get("images") or []
            groups[sid] = (section.get("headline"), [(images, i) for i in range(len(images))])
        elif component == "showcase":
            for group in section.get("groups") or []:
                images = group.get("images") or []
                groups[str(group["label"])] = (
                    group.get("caption"),
                    [(images, i) for i in range(len(images))],
                )
        elif component in {"hero-carousel", "quiz-hero"}:
            items = section.get("slides" if component == "hero-carousel" else "choices") or []
            groups[sid] = (
                f"{component} imagery",
                [(item, "image") for item in items if "image" in item],
            )
    return groups


def landing_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_section_ids(sections: list[dict[str, Any]]) -> list[str]:
    """Port of the lab's resolveSectionIds: explicit ids win, then component, component-2, ..."""
    taken = {str(section["id"]) for section in sections if section.get("id")}
    out: list[str] = []
    for section in sections:
        if section.get("id"):
            out.append(str(section["id"]))
            continue
        component = str(section.get("component"))
        candidate = component
        n = 1
        while candidate in taken:
            n += 1
            candidate = f"{component}-{n}"
        taken.add(candidate)
        out.append(candidate)
    return out


def _strip_section(section: dict[str, Any], section_id: str) -> dict[str, Any]:
    out: dict[str, Any] = {"id": section_id, "component": section.get("component")}
    out["canReorder"] = section.get("component") in MOVABLE_COMPONENTS
    out["canOmit"] = section.get("component") in OMITTABLE_COMPONENTS
    if section.get("component") == "growth-block":
        out.update({k: section.get(k) for k in ("kind", "headline", "body", "ctaLabel", "layout", "items", "images")})
        out["canReorder"] = section.get("kind") not in {"hero", "cta"}
    if section.get("component") == "hero-carousel":
        slides = section.get("slides") or []
        out["slides"] = slides
        if slides:
            out.update(
                {
                    key: slides[0][key]
                    for key in ("headline", "subhead", "ctaLabel")
                    if key in slides[0]
                }
            )
    for key in ("headline", "subhead", "ctaLabel", "reassurance", "layout", "stickyCta"):
        if key in section:
            out[key] = section[key]
    if "video" in section:
        out["hasVideo"] = True
        label = (section.get("video") or {}).get("label")
        if label:
            out["videoLabel"] = label
    if "logos" in section:
        out["logoCount"] = len(section["logos"] or [])
    if "quotes" in section:
        out["quoteCount"] = len(section["quotes"] or [])
    if "plans" in section:
        out["planNames"] = [plan.get("name") for plan in section["plans"] or []]
    if "groups" in section:
        groups = section["groups"] or []
        out["groupLabels"] = [group.get("label") for group in groups]
        out["groups"] = [
            {"label": group.get("label"), "imageCount": len(group.get("images") or [])}
            for group in groups
        ]
    return out


def get_current_landing(settings: Settings) -> dict[str, Any]:
    raw = _yaml.load(settings.landing_path.read_text(encoding="utf-8")) or {}
    props = raw.get("props") or {}
    raw_sections = list(props.get("sections") or [])
    ids = resolve_section_ids(raw_sections)
    sections = [_strip_section(section, sid) for section, sid in zip(raw_sections, ids)]
    return {
        "version": settings.base_version,
        "hash": landing_hash(settings.landing_path),
        "pageTitle": props.get("pageTitle"),
        "growthDesign": props.get("growthDesign"),
        "rawSections": raw_sections,
        "header": props.get("header"),
        "footer": props.get("footer"),
        "mobile": props.get("mobile"),
        "sections": sections,
        "imageGroups": [
            {"label": label, "caption": caption, "imageCount": len(targets)}
            for label, (caption, targets) in landing_image_targets(raw_sections).items()
        ],
    }
