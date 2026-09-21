"""Write proposal changes into a variant's landing.yaml with a ruamel round-trip."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .landing import resolve_section_ids
from .landing_diff import ApplyError, ProducedFiles
from .models import (
    CompositionChanges,
    HeroCopyChanges,
    InlineCtaCopy,
    RedesignChanges,
    SectionCopy,
)

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096
# Match the pricing-lab's hand-written style so a variant diffs only where copy changed.
_yaml.indent(mapping=2, sequence=4, offset=2)


def load_yaml(path: Path) -> Any:
    return _yaml.load(path.read_text(encoding="utf-8"))


def dump_yaml(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        _yaml.dump(data, handle)


@dataclass(frozen=True)
class ProducedMedia:
    """What the media stage produced: the hero video object and showcase refs by (label, slot)."""

    hero_video_ref: dict[str, Any] | None = None
    showcase: dict[tuple[str, int], str] = field(default_factory=dict)
    files: ProducedFiles = field(default_factory=ProducedFiles)
    cached_files: int = 0
    tiles: list[Any] = field(default_factory=list)  # media.TileResult per generated tile


def _apply_copy(section: dict[str, Any], copy: SectionCopy | HeroCopyChanges) -> None:
    if copy.headline is not None:
        section["headline"] = copy.headline
    if copy.subhead is not None:
        section["subhead"] = copy.subhead
    if copy.cta_label is not None:
        section["ctaLabel"] = copy.cta_label
    if copy.reassurance is not None:
        section["reassurance"] = copy.reassurance


def _apply_inline(section: dict[str, Any], copy: InlineCtaCopy) -> None:
    if copy.headline is not None:
        section["headline"] = copy.headline
    if copy.cta_label is not None:
        section["ctaLabel"] = copy.cta_label


def patch_hero_copy(landing_path: Path, changes: HeroCopyChanges) -> None:
    doc = load_yaml(landing_path)
    sections = ((doc.get("props") or {}).get("sections")) or []
    for section in sections:
        component = section.get("component")
        if component == "kittl-hero":
            _apply_copy(section, changes)
        elif component == "video-cta" and changes.video_cta:
            _apply_copy(section, changes.video_cta)
        elif component == "final-cta" and changes.final_cta:
            _apply_copy(section, changes.final_cta)
    dump_yaml(landing_path, doc)


def compose_order(ids: list[str], composition: CompositionChanges | None) -> list[str]:
    """Section ids in their new order: hero first, omissions removed, `order` for the rest."""
    if composition is None:
        return list(ids)
    known = set(ids)
    omit = list(composition.omit or [])
    for sid in omit:
        if sid not in known:
            raise ApplyError(f"composition: unknown section id {sid!r} in omit")
    survivors = [sid for sid in ids if sid not in omit]
    if not composition.order:
        return survivors
    order = list(composition.order)
    for sid in order:
        if sid not in known:
            raise ApplyError(f"composition: unknown section id {sid!r} in order")
    expected = set(survivors[1:])
    if set(order) != expected:
        raise ApplyError(
            "composition.order must list every remaining section after the hero exactly once: "
            f"missing {sorted(expected - set(order))}, extra {sorted(set(order) - expected)}"
        )
    return [survivors[0]] + order


def _video_map(ref: dict[str, Any]) -> CommentedMap:
    video = CommentedMap()
    video["label"] = ref["label"]
    for key in ("desktop", "phone"):
        source = CommentedMap()
        for name in ("webm", "mp4", "poster", "aspect"):
            if name in ref[key]:
                source[name] = ref[key][name]
        video[key] = source
    return video


def patch_redesign(
    landing_path: Path,
    changes: RedesignChanges,
    media: ProducedMedia | None = None,
) -> list[str]:
    """Apply composition, copy, layout and media; return the resulting section order."""
    doc = load_yaml(landing_path)
    props = doc.get("props") or {}
    seq = props.get("sections")
    if not seq:
        raise ApplyError("landing.yaml has no sections")
    ids = resolve_section_ids(list(seq))
    by_id = dict(zip(ids, list(seq)))
    order = compose_order(ids, changes.composition)
    if order != ids:
        items = [by_id[sid] for sid in order]
        # Rebuild in place: replacing the sequence would drop the comments ruamel keeps on it,
        # and per-index comments would land on the wrong item after a reorder.
        comments = getattr(getattr(seq, "ca", None), "items", None)
        if comments is not None:
            comments.clear()
        del seq[:]
        for item in items:
            seq.append(item)

    copy = changes.copy_changes
    for section in seq:
        component = section.get("component")
        if copy is not None:
            if component == "kittl-hero" and copy.hero:
                _apply_copy(section, copy.hero)
            elif component == "video-cta" and copy.video_cta:
                _apply_copy(section, copy.video_cta)
            elif component == "final-cta" and copy.final_cta:
                _apply_copy(section, copy.final_cta)
            elif component == "inline-cta" and copy.inline_cta:
                _apply_inline(section, copy.inline_cta)
        if component == "kittl-hero" and changes.layout is not None:
            if changes.layout.layout is not None:
                section["layout"] = changes.layout.layout
            if changes.layout.sticky_cta is not None:
                section["stickyCta"] = changes.layout.sticky_cta

    if media is not None:
        if media.hero_video_ref:
            hero = next(
                (section for section in seq if section.get("component") == "kittl-hero"), None
            )
            if hero is None:
                raise ApplyError("landing.yaml has no kittl-hero section for the clip")
            hero["video"] = _video_map(media.hero_video_ref)
        for (label, slot), ref in media.showcase.items():
            group = None
            for section in seq:
                if section.get("component") != "showcase":
                    continue
                for candidate in section.get("groups") or []:
                    if candidate.get("label") == label:
                        group = candidate
                        break
                if group is not None:
                    break
            if group is None:
                raise ApplyError(f"media: showcase group {label!r} not found on the landing")
            images = group.get("images") or []
            if slot >= len(images):
                raise ApplyError(f"media: showcase group {label!r} has no slot {slot}")
            images[slot] = ref
    dump_yaml(landing_path, doc)
    return order
