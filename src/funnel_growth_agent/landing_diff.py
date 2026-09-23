"""Hard-diff allowlist for a variant: identity-aware landing diff plus the file-set rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .landing import HERO_COMPONENTS, landing_image_targets, resolve_section_ids
from .models import MAX_OMIT, MOVABLE_COMPONENTS, OMITTABLE_COMPONENTS

_safe = YAML(typ="safe")

FUNNEL_ALLOWED = {"id", "landingUser", "title"}
TEXT_ALLOWED: dict[str, set[str]] = {
    "kittl-hero": {"headline", "subhead", "ctaLabel", "reassurance", "layout", "stickyCta"},
    "video-cta": {"headline", "subhead", "ctaLabel", "reassurance"},
    "final-cta": {"headline", "subhead", "ctaLabel", "reassurance"},
    "inline-cta": {"headline", "ctaLabel"},
    "quiz-hero": {"headline", "subhead"},
}
NEW_FILE_RE = re.compile(
    r"^assets/(video|showcase|thumbs/showcase)/[a-z0-9][a-z0-9-]*\.(mp4|webm|webp)$"
)
THUMB_PREFIX = "assets/thumbs/showcase/"
SHOWCASE_IMAGE_RE = re.compile(r"^groups\[(\d+)\]\.images\[(\d+)\]$")
VIDEO_SOURCE_KEYS = {"webm", "mp4", "poster"}
MAX_ASSET_BYTES = 6_000_000


class ApplyError(ValueError):
    pass


@dataclass(frozen=True)
class ProducedFiles:
    """Files the media stage created inside the variant, relative to the variant dir."""

    hero_video: frozenset[str] = frozenset()
    showcase: frozenset[str] = frozenset()
    showcase_thumbs: frozenset[str] = frozenset()

    @property
    def all(self) -> frozenset[str]:
        return self.hero_video | self.showcase | self.showcase_thumbs


def thumb_sibling(rel: str) -> str:
    """`assets/thumbs/showcase/x.webp` -> `assets/showcase/x.webp`."""
    return "assets/showcase/" + rel[len(THUMB_PREFIX) :]


def walk_files(root: Path) -> dict[str, Path]:
    return {str(path.relative_to(root)): path for path in root.rglob("*") if path.is_file()}


def load_safe(path: Path) -> Any:
    return _safe.load(path.read_text(encoding="utf-8"))


def changed_keys(old: Any, new: Any, prefix: str = "") -> list[str]:
    if old == new:
        return []
    if type(old) is not type(new):
        return [prefix or "$"]
    if isinstance(old, dict):
        keys = set(old) | set(new)
        out: list[str] = []
        for key in sorted(keys, key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in old or key not in new:
                out.append(path)
            else:
                out.extend(changed_keys(old[key], new[key], path))
        return out
    if isinstance(old, list):
        if len(old) != len(new):
            return [prefix or "$"]
        out = []
        for index, (left, right) in enumerate(zip(old, new)):
            out.extend(changed_keys(left, right, f"{prefix}[{index}]"))
        return out
    return [prefix or "$"]


def _top_key(path: str) -> str:
    """`headline[0]` and `video.desktop.mp4` both belong to their first key."""
    return re.split(r"[.\[]", path, maxsplit=1)[0]


def _sections(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return list(((doc.get("props") or {}).get("sections")) or [])


def _without_sections(doc: dict[str, Any]) -> dict[str, Any]:
    props = dict(doc.get("props") or {})
    props.pop("sections", None)
    rest = {key: value for key, value in doc.items() if key != "props"}
    rest["props"] = props
    return rest


def _check_video_ref(video: Any, produced: ProducedFiles) -> set[str]:
    if not produced.hero_video:
        raise ApplyError("hard-diff allowlist: hero video changed without produced files")
    if not isinstance(video, dict) or set(video) != {"label", "desktop", "phone"}:
        raise ApplyError("hard-diff allowlist: hero video must be exactly {label, desktop, phone}")
    if not isinstance(video.get("label"), str) or not video["label"].strip():
        raise ApplyError("hard-diff allowlist: hero video needs a label")
    refs: set[str] = set()
    for key, width in (("desktop", "1280"), ("phone", "720")):
        source = video.get(key)
        if not isinstance(source, dict):
            raise ApplyError(f"hard-diff allowlist: hero video {key} must be an object")
        keys = set(source)
        if not VIDEO_SOURCE_KEYS <= keys or not keys <= VIDEO_SOURCE_KEYS | {"aspect"}:
            raise ApplyError(f"hard-diff allowlist: hero video {key} keys {sorted(keys)}")
        for name in VIDEO_SOURCE_KEYS:
            ref = source[name]
            if not isinstance(ref, str) or ref not in produced.hero_video:
                raise ApplyError(
                    f"hard-diff allowlist: hero video {key}.{name} -> {ref!r} was not produced"
                )
            if f"-{width}." not in ref:
                raise ApplyError(
                    f"hard-diff allowlist: hero video {key}.{name} must use the {width} rendition"
                )
            refs.add(ref)
    if refs != set(produced.hero_video):
        raise ApplyError("hard-diff allowlist: hero video must reference every produced clip file")
    return refs


def check_landing_diff(
    old_doc: dict[str, Any],
    new_doc: dict[str, Any],
    produced: ProducedFiles | None = None,
) -> list[str]:
    """Return a human summary of allowed changes; raise ApplyError on anything else."""
    produced = produced or ProducedFiles()
    if (new_doc.get("props") or {}).get("growthDesign"):
        from .blueprint import validate_rebuild_diff
        return validate_rebuild_diff(old_doc, new_doc, produced)
    outside = changed_keys(_without_sections(old_doc), _without_sections(new_doc))
    if outside:
        raise ApplyError(f"hard-diff allowlist: landing.yaml changed outside sections {outside}")
    old_sections = _sections(old_doc)
    new_sections = _sections(new_doc)
    old_ids = resolve_section_ids(old_sections)
    new_ids = resolve_section_ids(new_sections)
    if len(set(new_ids)) != len(new_ids):
        raise ApplyError("hard-diff allowlist: duplicate section ids in the variant")
    unknown = [sid for sid in new_ids if sid not in old_ids]
    if unknown:
        raise ApplyError(f"hard-diff allowlist: variant invents sections {unknown}")
    old_by = dict(zip(old_ids, old_sections))
    new_by = dict(zip(new_ids, new_sections))

    def component(sid: str) -> str:
        return str(old_by[sid].get("component"))

    omitted = [sid for sid in old_ids if sid not in new_by]
    for sid in omitted:
        if component(sid) not in OMITTABLE_COMPONENTS:
            raise ApplyError(f"hard-diff allowlist: {sid} cannot be omitted")
    if len(omitted) > MAX_OMIT:
        raise ApplyError(f"hard-diff allowlist: at most {MAX_OMIT} sections may be omitted")
    if not new_ids or new_ids[0] != old_ids[0] or component(new_ids[0]) not in HERO_COMPONENTS:
        raise ApplyError("hard-diff allowlist: kittl-hero must stay the first section")
    if (
        any(component(sid) == "final-cta" for sid in old_ids)
        and component(new_ids[-1]) != "final-cta"
    ):
        raise ApplyError("hard-diff allowlist: final-cta must stay the last section")
    fixed_old = [
        sid for sid in old_ids if sid in new_by and component(sid) not in MOVABLE_COMPONENTS
    ]
    fixed_new = [sid for sid in new_ids if component(sid) not in MOVABLE_COMPONENTS]
    if fixed_old != fixed_new:
        raise ApplyError("hard-diff allowlist: only movable sections may change position")

    summary: list[str] = []
    surviving_old_order = [sid for sid in old_ids if sid in new_by]
    if new_ids != surviving_old_order:
        summary.append("sections reordered: " + ", ".join(new_ids))
    if omitted:
        summary.append("sections omitted: " + ", ".join(omitted))

    referenced: set[str] = set()
    for sid in new_ids:
        old_section = old_by[sid]
        new_section = new_by[sid]
        paths = changed_keys(old_section, new_section)
        if not paths:
            continue
        kind = component(sid)
        if new_section.get("component") != kind:
            raise ApplyError(f"hard-diff allowlist: {sid} changed component")
        allowed = TEXT_ALLOWED.get(kind)
        if kind == "kittl-hero":
            video_paths = [path for path in paths if _top_key(path) == "video"]
            text_paths = [path for path in paths if path not in video_paths]
            bad = [path for path in text_paths if _top_key(path) not in (allowed or set())]
            if bad:
                raise ApplyError(f"hard-diff allowlist: {sid} changed {bad}")
            if video_paths:
                referenced |= _check_video_ref(new_section.get("video"), produced)
            layout_touched = "layout" in text_paths or bool(video_paths)
            if (
                layout_touched
                and new_section.get("layout") == "video-first"
                and not new_section.get("video")
            ):
                raise ApplyError("hard-diff allowlist: layout video-first needs a hero video")
        elif kind in {"hero-carousel", "quiz-hero"}:
            collection = "slides" if kind == "hero-carousel" else "choices"
            for path in paths:
                match = re.fullmatch(rf"{collection}\[(\d+)\]\.image", path)
                if match:
                    value = new_section[collection][int(match.group(1))]["image"]
                    if value not in produced.showcase:
                        raise ApplyError(f"hard-diff allowlist: {sid} image was not produced")
                    referenced.add(value)
                elif kind == "hero-carousel" and re.fullmatch(
                    r"slides\[\d+\]\.(headline(?:\[\d+\])?|subhead|ctaLabel)", path
                ):
                    continue
                elif kind == "quiz-hero" and _top_key(path) in TEXT_ALLOWED[kind]:
                    continue
                else:
                    raise ApplyError(f"hard-diff allowlist: {sid} changed {path}")
        elif kind == "showcase":
            for path in paths:
                match = SHOWCASE_IMAGE_RE.match(path)
                if not match:
                    raise ApplyError(
                        f"hard-diff allowlist: showcase may only swap images, changed {path}"
                    )
                group_index, slot = int(match.group(1)), int(match.group(2))
                value = new_section["groups"][group_index]["images"][slot]
                if not isinstance(value, str) or value not in produced.showcase:
                    raise ApplyError(
                        f"hard-diff allowlist: showcase image {value!r} was not produced"
                    )
                referenced.add(value)
        elif allowed is not None:
            bad = [path for path in paths if _top_key(path) not in allowed]
            if bad:
                raise ApplyError(f"hard-diff allowlist: {sid} changed {bad}")
        else:
            raise ApplyError(f"hard-diff allowlist: {sid} ({kind}) cannot change: {paths}")
        summary.append(f"{sid}: " + ", ".join(paths))

    # Reusing a generated asset from a published baseline need not change its YAML reference.
    for _caption, targets in landing_image_targets(new_sections).values():
        referenced.update(
            container[key] for container, key in targets if container[key] in produced.showcase
        )
    for section in new_sections:
        if section.get("component") == "kittl-hero":
            for source in (section.get("video") or {}).values():
                if isinstance(source, dict):
                    referenced.update(
                        value
                        for value in source.values()
                        if isinstance(value, str) and value in produced.hero_video
                    )
    # A thumb is never referenced by YAML; it rides along with its referenced full-size sibling.
    for thumb in produced.showcase_thumbs:
        if not thumb.startswith(THUMB_PREFIX) or thumb_sibling(thumb) not in produced.showcase:
            raise ApplyError(f"hard-diff allowlist: thumb {thumb} has no produced showcase sibling")
    allowed_thumbs = {t for t in produced.showcase_thumbs if thumb_sibling(t) in referenced}
    orphans = set(produced.all) - referenced - allowed_thumbs
    if orphans:
        raise ApplyError(f"hard-diff allowlist: produced files not referenced {sorted(orphans)}")
    return summary


def hard_diff_variant(
    base_dir: Path,
    variant_dir: Path,
    produced: ProducedFiles | None = None,
) -> list[str]:
    """The variant may differ from the base only in funnel.yaml identity, landing.yaml
    allowed fields, and the produced media files under assets/."""
    produced = produced or ProducedFiles()
    base_files = walk_files(base_dir)
    variant_files = walk_files(variant_dir)
    removed = set(base_files) - set(variant_files)
    if removed:
        raise ApplyError(f"hard-diff allowlist: variant removed base files {sorted(removed)}")
    extra = set(variant_files) - set(base_files)
    expected_extra = set(produced.all) - set(base_files)
    if extra != expected_extra:
        raise ApplyError(
            "hard-diff allowlist: variant file set differs from the base version: "
            f"unexpected {sorted(extra - produced.all)}, missing {sorted(expected_extra - extra)}"
        )
    for rel in sorted(extra):
        if not NEW_FILE_RE.match(rel):
            raise ApplyError(
                f"hard-diff allowlist: new file outside assets/video|showcase|thumbs/showcase: {rel}"
            )
        size = variant_files[rel].stat().st_size
        if size == 0 or size > MAX_ASSET_BYTES:
            raise ApplyError(f"hard-diff allowlist: {rel} is {size} bytes (max {MAX_ASSET_BYTES})")
    summary: list[str] = []
    for rel, base_path in base_files.items():
        variant_path = variant_files[rel]
        if base_path.read_bytes() == variant_path.read_bytes():
            continue
        if rel == "funnel.yaml":
            changed = changed_keys(load_safe(base_path), load_safe(variant_path))
            if any(key not in FUNNEL_ALLOWED for key in changed):
                raise ApplyError(f"hard-diff allowlist: funnel.yaml changed {changed}")
            continue
        if rel == "steps/landing.yaml":
            summary.extend(
                check_landing_diff(load_safe(base_path), load_safe(variant_path), produced)
            )
            continue
        raise ApplyError(f"hard-diff allowlist: unexpected change in {rel}")
    return summary
