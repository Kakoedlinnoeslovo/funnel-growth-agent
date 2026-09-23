"""Versioned page composition and level enforcement shared by preview and apply."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from .models import PageBlueprint

CONTRACT = "growth-blocks-v1"
CAMPAIGN_CONTRACT = "growth-blocks-v2"
FUNCTIONAL = {"quiz-hero", "plan-preview"}
PROTECTED = {"quiz-hero", "plan-preview", "press-quotes", "logo-strip"}
RECIPES = [
    {
        "id": "results-first",
        "label": "Results first",
        "theme": "clean-light",
        "layout": "split",
        "body": ["gallery", "features", "steps", "proof"],
    },
    {
        "id": "workflow-first",
        "label": "Workflow first",
        "theme": "dark-showcase",
        "layout": "media-first",
        "body": ["steps", "gallery", "features", "faq"],
    },
    {
        "id": "proof-first",
        "label": "Proof first",
        "theme": "warm-editorial",
        "layout": "centered",
        "body": ["proof", "features", "comparison", "faq"],
    },
]


def capabilities(lab: Path) -> dict:
    path = lab / "funnels/growth-capabilities.json"
    if not path.is_file():
        return {"contract": None, "available": False}
    result = json.loads(path.read_text())
    result["available"] = (result.get("contract"), result.get("schemaVersion", 1)) in {
        (CONTRACT, 1),
        (CAMPAIGN_CONTRACT, 2),
    }
    result["proposalSchema"] = PageBlueprint.model_json_schema(by_alias=True)
    return result


def require_renderer(lab: Path, schema_version: int = 1) -> None:
    capability = capabilities(lab)
    if not capability.get("available") or (
        schema_version == 2 and capability.get("contract") != CAMPAIGN_CONTRACT
    ):
        raise ValueError(
            f"This redesign requires growth-blocks-v{schema_version} on the pricing lab/deployment branch. Install the shared renderer first; the reviewed page was preserved."
        )


def image_refs(value) -> set[str]:
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"image", "src", "poster", "thumb"} and isinstance(item, str):
                found.add(item)
            elif key == "images" and isinstance(item, list):
                found.update(v for v in item if isinstance(v, str))
            else:
                found.update(image_refs(item))
    elif isinstance(value, list):
        for item in value:
            found.update(image_refs(item))
    return found


def compose_document(
    original: dict, page: PageBlueprint, produced=None, *, placeholders=False
) -> dict:
    from .landing import resolve_section_ids

    result = deepcopy(original)
    props = result.setdefault("props", {})
    old = props.get("sections", [])
    by_id = dict(zip(resolve_section_ids(old), old))
    allowed_images = image_refs(old)
    planned = (
        {(p.group, p.slot) for p in page.media.showcase + page.media.sourced}
        if page.media
        else set()
    )
    known = {b.id for b in page.blocks}
    if any(group not in known for group, _ in planned):
        raise ValueError("Generated images must name a blueprint block id")
    sections, used = [], set()
    for block in page.blocks:
        if block.kind != "proof" and by_id.get(block.id, {}).get("component") in PROTECTED:
            raise ValueError(
                "This block id is reserved for a protected quiz, offer or proof section"
            )
        if block.hidden:
            continue
        if block.kind == "proof":
            source = by_id.get(block.source_section_id)
            if not source or source.get("component") not in {
                "press-quotes",
                "logo-strip",
                "testimonials",
                "quote",
                "growth-block",
            }:
                raise ValueError(
                    "Proof requires an existing verified proof section sourceSectionId"
                )
            if source.get("component") == "growth-block":
                raise ValueError(
                    "Use a preserved proof source; generated copy is not testimonial evidence"
                )
            if block.source_section_id in used:
                raise ValueError("Proof source repeated")
            sections.append(deepcopy(source))
            used.add(block.source_section_id)
            continue
        row = block.model_dump(
            by_alias=True, exclude={"source_section_id", "video_source_section_id", "hidden"}
        )
        row["component"] = "growth-block"
        if page.schema_version == 1:
            row.pop("variant", None)
            row.pop("eyebrow", None)
        for ref in row["images"]:
            if ref not in allowed_images:
                raise ValueError(
                    f"Blueprint image {ref!r} is not an existing baseline asset: images may only "
                    "repeat an asset path exactly as it appears in the baseline rawSections. To "
                    "show a new image instead, leave it out of images and plan it as "
                    f"media.showcase with group {block.id!r} and its slot index."
                )
        slots = sorted(slot for group, slot in planned if group == block.id)
        for slot in slots:
            while len(row["images"]) <= slot:
                row["images"].append("")
            if produced and (block.id, slot) in produced.showcase:
                row["images"][slot] = produced.showcase[block.id, slot]
            elif not placeholders:
                raise ValueError("Blueprint image plan was not produced")
        if block.video_source_section_id:
            source = by_id.get(block.video_source_section_id, {})
            if block.kind != "hero" or not source.get("video"):
                raise ValueError("Video source must identify an existing hero video")
            row["video"] = deepcopy(source["video"])
        if block.kind == "hero" and produced and produced.hero_video_ref:
            row["video"] = produced.hero_video_ref
        row["placeholder"] = bool(
            placeholders
            and (slots or not row["images"])
            and block.kind in {"hero", "gallery", "comparison"}
        )
        # Never persist empty asset strings: a labeled placeholder is a renderer feature.
        row["images"] = [ref for ref in row["images"] if ref]
        existing_generated = {ref for s in old for ref in s.get("generatedImages", [])}
        generated = (
            set(produced.showcase.values()) - set(produced.sourced_credits) if produced else set()
        )
        credits = {c["image"]: c for section in old for c in section.get("credits", [])}
        if produced:
            credits.update(produced.sourced_credits)
        if any(ref in credits for ref in row["images"]):
            row["credits"] = [credits[ref] for ref in row["images"] if ref in credits]
        row["generatedImages"] = [
            ref for ref in row["images"] if ref in existing_generated | generated
        ]
        sections.append(row)
    preserved = [
        deepcopy(row)
        for sid, row in by_id.items()
        if row.get("component") in (PROTECTED if page.schema_version == 1 else FUNCTIONAL)
        and sid not in used
    ]
    sections[-1:-1] = preserved
    props["growthDesign"] = {
        "schemaVersion": page.schema_version,
        "theme": page.theme,
        "artDirection": page.art_direction,
    }
    if page.tokens:
        props["growthDesign"]["tokens"] = page.tokens.model_dump(by_alias=True)
    props["sections"] = sections
    return result


def validate_rebuild_diff(old: dict, new: dict, produced) -> list[str]:
    from .landing import resolve_section_ids
    from .landing_diff import ApplyError, _check_video_ref

    a, b = deepcopy(old), deepcopy(new)
    old_sections = a.get("props", {}).pop("sections", [])
    new_sections = b.get("props", {}).pop("sections", [])
    a.get("props", {}).pop("growthDesign", None)
    design = b.get("props", {}).pop("growthDesign", None)
    if (
        a != b
        or not design
        or design.get("schemaVersion") not in {1, 2}
        or design.get("theme") not in {r["theme"] for r in RECIPES}
    ):
        raise ApplyError("Rebuild changed fields outside the versioned landing composition")
    old_ids = dict(zip(resolve_section_ids(old_sections), old_sections))
    for row in old_sections:
        if (
            row.get("component") in (PROTECTED if design["schemaVersion"] == 1 else FUNCTIONAL)
            and row not in new_sections
        ):
            raise ApplyError("Rebuild changed protected quiz, offer or proof data")
    allowed = image_refs(old_sections) | set(produced.all)
    if not image_refs(new_sections) <= allowed:
        raise ApplyError("Rebuild introduced an unapproved image reference")
    from .models import PageBlock

    if len(resolve_section_ids(new_sections)) != len(set(resolve_section_ids(new_sections))):
        raise ApplyError("Rebuild section ids must be unique")
    for row in new_sections:
        if row.get("component") == "growth-block":
            if row.get("kind") == "proof":
                raise ApplyError("New proof cannot replace verified baseline evidence")
            PageBlock.model_validate(
                {
                    k: v
                    for k, v in row.items()
                    if k not in {"component", "video", "placeholder", "generatedImages", "credits"}
                }
            )
            if row.get("video"):
                old_videos = [x.get("video") for x in old_sections if x.get("video")]
                if row["video"] not in old_videos:
                    _check_video_ref(row["video"], produced)
        elif row not in old_ids.values():
            raise ApplyError("Rebuild introduced or modified an unsupported legacy section")
    if new_sections[0].get("kind") != "hero" or new_sections[-1].get("kind") != "cta":
        raise ApplyError("Rebuild must retain a hero and final CTA")
    return [
        "New landing composition",
        f"Theme: {design['theme']}",
        f"{len(new_sections)} sections; checkout and proof preserved",
    ]


def enforce_level(previous: dict | None, current: dict, level: str) -> None:
    if level == "heavy":
        if "blocks" not in current:
            raise ValueError("Heavy requires a full page blueprint, not small field changes")
        if previous and "blocks" in previous:
            before, after = previous["blocks"], current["blocks"]
            if (
                previous.get("tokens") == current.get("tokens")
                and previous.get("theme") == current.get("theme")
                and before[0].get("layout") == after[0].get("layout")
            ):
                raise ValueError("Heavy must change the hero composition or visual treatment")
            kinds_before = [b["kind"] for b in before[1:-1]]
            kinds_after = [b["kind"] for b in after[1:-1]]
            differences = sum(a != b for a, b in zip(kinds_before, kinds_after)) + abs(
                len(kinds_before) - len(kinds_after)
            )
            if differences < 2:
                raise ValueError(
                    "Heavy must recompose at least two body sections, not only rewrite copy"
                )
        return
    previous = previous or {}
    if "blocks" in current or "blocks" in previous:
        if not ("blocks" in current and "blocks" in previous):
            raise ValueError("Choose Heavy to replace the page architecture")
        if (
            current.get("theme") != previous.get("theme")
            or current.get("tokens") != previous.get("tokens")
            or current.get("schemaVersion") != previous.get("schemaVersion")
        ):
            raise ValueError("Choose Heavy to change the page theme")
        if level == "medium":
            before = {b["id"]: b["kind"] for b in previous["blocks"]}
            if {b["id"]: b["kind"] for b in current["blocks"]} != before:
                raise ValueError("Medium preserves existing block types and identities")
            return
        text_keys = {"headline", "body", "title", "ctaLabel", "subhead", "reassurance"}

        def without_text(value):
            if isinstance(value, dict):
                return {
                    k: without_text(v)
                    for k, v in value.items()
                    if k not in text_keys and v is not None
                }
            if isinstance(value, list):
                return [without_text(v) for v in value]
            return value

        if without_text(previous) != without_text(current):
            raise ValueError("Light changes copy only; preserve layout, media and art direction")
    elif level == "light":
        # Legacy hero_copy proposals are flat text-only data.
        hero_fields = {"headline", "subhead", "ctaLabel", "reassurance"}
        if set(current) <= hero_fields and set(previous) <= hero_fields:
            return
        for key in set(previous) | set(current):
            if key != "copy" and previous.get(key) != current.get(key):
                raise ValueError("Light changes copy only; preserve layout and media")


def baseline_blueprint(path: Path) -> dict | None:
    """Recover an already-built design when it becomes a new draft's baseline."""
    from .landing import resolve_section_ids
    from .landing_patch import load_yaml

    props = load_yaml(path).get("props", {})
    if not props.get("growthDesign"):
        return None
    blocks = []
    rows = props.get("sections", [])
    for sid, row in zip(resolve_section_ids(rows), rows):
        if row.get("component") == "growth-block":
            blocks.append(
                {
                    k: v
                    for k, v in row.items()
                    if k not in {"component", "video", "placeholder", "generatedImages", "credits"}
                }
            )
            if row.get("video"):
                blocks[-1]["videoSourceSectionId"] = sid
        elif row.get("component") in {"press-quotes", "logo-strip"}:
            blocks.append(
                {"id": sid, "kind": "proof", "headline": "Existing proof", "sourceSectionId": sid}
            )
    return PageBlueprint.model_validate(
        {
            **props["growthDesign"],
            "artDirection": props["growthDesign"].get(
                "artDirection", "Preserve the existing art direction"
            ),
            "blocks": blocks,
        }
    ).model_dump(by_alias=True, exclude_none=True)


def summarize_changes(before: dict, after: dict) -> list[str]:
    from .landing import resolve_section_ids

    old, new = before.get("props", {}), after.get("props", {})
    old_rows, new_rows = old.get("sections", []), new.get("sections", [])
    old_ids, new_ids = resolve_section_ids(old_rows), resolve_section_ids(new_rows)
    old_map = dict(zip(old_ids, old_rows))
    summary = []
    if old.get("growthDesign") != new.get("growthDesign") and new.get("growthDesign"):
        summary.append("Visual treatment: " + new["growthDesign"]["theme"].replace("-", " "))
    if old_ids != new_ids:
        added, removed = len(set(new_ids) - set(old_ids)), len(set(old_ids) - set(new_ids))
        summary.append(
            f"Replaced page sections: {added} added, {removed} removed"
            if added or removed
            else "Reordered existing sections"
        )
    for sid, row in zip(new_ids, new_rows):
        previous = old_map.get(sid)
        label = row.get("kind", row.get("component", sid)).replace("-", " ")
        if previous is None:
            summary.append("Added " + label)
        elif previous != row:
            keys = {k for k in set(previous) | set(row) if previous.get(k) != row.get(k)}
            detail = (
                "copy"
                if keys <= {"headline", "body", "subhead", "ctaLabel", "reassurance", "items"}
                else "imagery"
                if keys <= {"images", "video", "groups", "generatedImages"}
                else "layout and content"
            )
            summary.append(f"Updated {detail} in {label}")
    return summary[:10] or ["Preserved the current design"]
