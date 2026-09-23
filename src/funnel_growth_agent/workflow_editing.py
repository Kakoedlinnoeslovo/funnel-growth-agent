"""Capability-driven editing of the existing landing contract."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from .landing import HERO_COMPONENTS
from .landing_patch import validate_landing_changes
from .models import (
    CAMEL,
    MAX_OMIT,
    CompositionChanges,
    CopyChanges,
    HeroLayoutChanges,
    HeroVideoPlan,
    PageBlueprint,
    RedesignChanges,
    ShowcaseImagePlan,
)


class MediaPatch(BaseModel):
    """Validate slot shapes now; validate cross-slot references after cumulative merging."""

    model_config = CAMEL
    hero_video: HeroVideoPlan | None = Field(default=None, alias="heroVideo")
    showcase: list[ShowcaseImagePlan] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique_slots(self):
        if len({(p.group, p.slot) for p in self.showcase}) != len(self.showcase):
            raise ValueError("Media edits repeat an image slot")
        return self


class ElementChanges(BaseModel):
    model_config = CAMEL
    page: PageBlueprint | None = None
    copy_changes: CopyChanges | None = Field(default=None, alias="copy")
    layout: HeroLayoutChanges | None = None
    composition: CompositionChanges | None = None
    media: MediaPatch | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if self.page and any((self.copy_changes, self.layout, self.composition, self.media)):
            raise ValueError("Edit either the page blueprint or legacy elements, not both")
        if not any(self.model_dump(by_alias=True, exclude_unset=True, exclude_none=True).values()):
            raise ValueError("Choose a landing element to change")
        return self


def merge_changes(previous: dict, patch: dict) -> dict:
    """Merge an element patch without discarding other sections or generated assets."""
    result = deepcopy(previous)
    for group, values in patch.items():
        if values is None:
            continue
        target = result.setdefault(group, {}) or {}
        if group == "copy":
            for section, fields in values.items():
                if fields is not None:
                    target[section] = {**(target.get(section) or {}), **fields}
        elif group == "media":
            if "heroVideo" in values:
                target["heroVideo"] = values["heroVideo"]
            if "showcase" in values:
                slots = {(p["group"], p["slot"]): p for p in target.get("showcase", [])}
                for item in values["showcase"]:
                    key = (item["group"], item["slot"])
                    slots[key] = {**slots.get(key, {}), **item}
                target["showcase"] = list(slots.values())
        else:
            target.update(values)
        result[group] = target
    return result


def editing_capabilities(baseline: dict, current: dict, proposal: dict, path: Path) -> dict:
    changes = (proposal or {}).get("changes") or {}
    if "blocks" in changes:
        return {"blueprint": changes, "sections": [], "presets": [], "order": [], "omit": []}
    current_sections = {s["id"]: s for s in current.get("sections", [])}
    media = (changes.get("media") or {}).get("showcase") or []
    plans = {(p["group"], p["slot"]): p for p in media}
    composition = changes.get("composition") or {}
    omitted = composition.get("omit") or []
    sections = []
    labels = {
        "headline": "Headline",
        "subhead": "Supporting copy",
        "ctaLabel": "Button label",
        "reassurance": "Reassurance",
    }
    for original in baseline.get("sections", []):
        sid = original["id"]
        row = current_sections.get(sid, original)
        kind = original["component"]
        copy_key = (
            "hero"
            if kind in HERO_COMPONENTS
            else {"video-cta": "videoCta", "final-cta": "finalCta", "inline-cta": "inlineCta"}.get(
                kind
            )
        )
        fields = list(labels) if copy_key else []
        if kind == "hero-carousel":
            fields.remove("reassurance")
        elif kind == "quiz-hero":
            fields = ["headline", "subhead"]
        elif kind == "inline-cta":
            fields = ["headline", "ctaLabel"]
        slots = []
        groups = row.get("groups") or []
        if kind in {"hero-carousel", "quiz-hero"}:
            groups = [g for g in baseline.get("imageGroups", []) if g["label"] == sid]
        for group in groups:
            for index in range(group.get("imageCount", 0)):
                slots.append(
                    {
                        "group": group["label"],
                        "slot": index,
                        "label": f"{group['label']} · image {index + 1}",
                        "plan": plans.get((group["label"], index)),
                    }
                )
        label = kind.replace("-", " ").capitalize()
        if kind == "inline-cta":
            label += " (copy applies to all inline CTAs)"
        if kind == "hero-carousel":
            label += " (copy applies to all slides)"
        sections.append(
            {
                "id": sid,
                "component": kind,
                "label": label,
                "visible": sid not in omitted,
                "canReorder": original.get("canReorder", False),
                "canOmit": original.get("canOmit", False),
                "copyKey": copy_key,
                "fields": [
                    {
                        "name": name,
                        "label": labels[name],
                        "value": row.get(name, ""),
                        "multiline": name in {"headline", "subhead"},
                    }
                    for name in fields
                    if name in row
                ],
                "layoutOptions": ["copy-first", "video-first"] if kind == "kittl-hero" else [],
                "layout": row.get("layout"),
                "stickyCta": row.get("stickyCta", False) if kind == "kittl-hero" else None,
                "mediaSlots": slots,
            }
        )
    order = [s["id"] for s in current.get("sections", [])[1:]]
    candidates = [
        {
            "id": "copy-first",
            "label": "Copy first",
            "changes": {"layout": {"layout": "copy-first"}},
        },
        {
            "id": "visual-first",
            "label": "Visual first",
            "changes": {"layout": {"layout": "video-first"}},
        },
    ]
    # Only permute movable slots. Fixed sections retain their exact position.
    by_id = {s["id"]: s for s in sections}
    movable = [sid for sid in order if by_id[sid]["canReorder"]]
    proof_order = sorted(
        movable, key=lambda sid: by_id[sid]["component"] not in {"logo-strip", "press-quotes"}
    )
    positions = iter(proof_order)
    proof = [next(positions) if by_id[sid]["canReorder"] else sid for sid in order]
    if proof != order:
        candidates.append(
            {
                "id": "proof-early",
                "label": "Proof early",
                "changes": {"composition": {"order": proof, "omit": omitted}},
            }
        )
    presets = []
    for preset in candidates:
        try:
            merged = RedesignChanges.model_validate(merge_changes(changes, preset["changes"]))
            validate_landing_changes(path, merged)
        except ValueError:
            continue
        presets.append(preset)
    available_layouts = [
        p["changes"]["layout"]["layout"] for p in presets if "layout" in p["changes"]
    ]
    for section in sections:
        if section["component"] == "kittl-hero":
            section["layoutOptions"] = available_layouts
    return {
        "sections": sections,
        "order": order,
        "omit": omitted,
        "presets": presets,
        "maxOmit": MAX_OMIT,
    }
