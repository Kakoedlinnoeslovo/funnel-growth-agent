"""Write proposal changes into a variant's landing.yaml with a ruamel round-trip."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .landing import HERO_COMPONENTS, landing_image_targets, resolve_section_ids
from .landing_diff import ApplyError, ProducedFiles, check_landing_diff, load_safe
from .models import (
    CompositionChanges,
    HeroCopyChanges,
    InlineCtaCopy,
    PageBlueprint,
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
    sourced_credits: dict[str, dict] = field(default_factory=dict)


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


def _apply_hero(section: dict[str, Any], copy: SectionCopy | HeroCopyChanges) -> None:
    kind = section.get("component")
    if kind == "hero-carousel":
        if copy.reassurance is not None:
            raise ApplyError("Carousel heroes do not have a reassurance field")
        for slide in section.get("slides") or []:
            _apply_copy(slide, copy)
    elif kind == "quiz-hero":
        if copy.cta_label is not None or copy.reassurance is not None:
            raise ApplyError("Quiz heroes use their existing choices as the CTA")
        if copy.headline is not None:
            section["headline"] = (
                " ".join(copy.headline) if isinstance(copy.headline, list) else copy.headline
            )
        if copy.subhead is not None:
            section["subhead"] = copy.subhead
    else:
        _apply_copy(section, copy)


def patch_hero_copy(landing_path: Path, changes: HeroCopyChanges) -> None:
    doc = load_yaml(landing_path)
    sections = ((doc.get("props") or {}).get("sections")) or []
    for section in sections:
        component = section.get("component")
        if component in HERO_COMPONENTS:
            _apply_hero(section, changes)
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
            if component in HERO_COMPONENTS and copy.hero:
                _apply_hero(section, copy.hero)
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
        elif component in HERO_COMPONENTS and changes.layout and not changes.layout.is_empty():
            raise ApplyError("Hero layout options only apply to kittl-hero baselines")

    if media is not None:
        if media.hero_video_ref:
            hero = next(
                (section for section in seq if section.get("component") == "kittl-hero"), None
            )
            if hero is None:
                raise ApplyError("landing.yaml has no kittl-hero section for the clip")
            hero["video"] = _video_map(media.hero_video_ref)
        for (label, slot), ref in media.showcase.items():
            group = landing_image_targets(list(seq)).get(label)
            if group is None:
                raise ApplyError(f"media: showcase group {label!r} not found on the landing")
            targets = group[1]
            if slot >= len(targets):
                raise ApplyError(f"media: showcase group {label!r} has no slot {slot}")
            container, key = targets[slot]
            container[key] = ref
    dump_yaml(landing_path, doc)
    return order


def validate_landing_changes(
    landing_path: Path, changes: RedesignChanges | HeroCopyChanges
) -> None:
    """Dry-run the real patch and allowlist before spending time on generated media.

    Placeholder references validate the planned media slots without creating any assets.
    The real apply still checks the actual files and runs the pricing-lab validator.
    """
    if isinstance(changes, PageBlueprint):
        from .blueprint import compose_document, require_renderer
        root = next(parent for parent in landing_path.parents if parent.name == "funnels").parent
        require_renderer(root, changes.schema_version)
        compose_document(load_yaml(landing_path), changes, placeholders=True)
        return
    with TemporaryDirectory(prefix="landing-preflight-") as folder:
        target = Path(folder) / "landing.yaml"
        target.write_bytes(landing_path.read_bytes())
        files = ProducedFiles()
        if isinstance(changes, RedesignChanges):
            video = None
            images = {}
            if changes.media:
                for index, tile in enumerate(changes.media.showcase):
                    images[tile.group, tile.slot] = f"assets/showcase/preflight-{index}.webp"
                if changes.media.hero_video:
                    video = {"label": changes.media.hero_video.label}
                    for size, width in (("desktop", 1280), ("phone", 720)):
                        video[size] = {
                            "mp4": f"assets/video/preflight-{width}.mp4",
                            "webm": f"assets/video/preflight-{width}.webm",
                            "poster": f"assets/video/preflight-{width}.webp",
                        }
            files = ProducedFiles(
                showcase=frozenset(images.values()),
                hero_video=frozenset(
                    ref
                    for size in ("desktop", "phone")
                    for ref in (video or {}).get(size, {}).values()
                ),
            )
            patch_redesign(target, changes, ProducedMedia(video, images, files))
        else:
            patch_hero_copy(target, changes)
        check_landing_diff(load_safe(landing_path), load_safe(target), files)
