"""Structured current landing. No media bytes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .config import Settings

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


def landing_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _strip_section(section: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"component": section.get("component")}
    for key in ("headline", "subhead", "ctaLabel", "reassurance", "layout", "stickyCta"):
        if key in section:
            out[key] = section[key]
    if "video" in section:
        out["hasVideo"] = True
    if "logos" in section:
        out["logoCount"] = len(section["logos"] or [])
    if "quotes" in section:
        out["quoteCount"] = len(section["quotes"] or [])
    if "plans" in section:
        out["planNames"] = [plan.get("name") for plan in section["plans"] or []]
    if "groups" in section:
        out["groupLabels"] = [group.get("label") for group in section["groups"] or []]
    return out


def get_current_landing(settings: Settings) -> dict[str, Any]:
    raw = _yaml.load(settings.landing_path.read_text(encoding="utf-8")) or {}
    props = raw.get("props") or {}
    sections = [_strip_section(section) for section in props.get("sections") or []]
    return {
        "version": settings.base_version,
        "hash": landing_hash(settings.landing_path),
        "pageTitle": props.get("pageTitle"),
        "header": props.get("header"),
        "footer": props.get("footer"),
        "sections": sections,
    }
