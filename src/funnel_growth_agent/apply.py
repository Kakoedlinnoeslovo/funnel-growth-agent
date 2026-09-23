"""Transactional apply: temp copy, media production, allowlist, two validates, atomic publish."""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ruamel.yaml import YAML

from .config import Settings
from .events import EmitData, emit_to
from .landing import landing_hash
from .landing_diff import (
    FUNNEL_ALLOWED,
    ApplyError,
    ProducedFiles,
    hard_diff_variant,
    walk_files,
)
from .landing_patch import ProducedMedia, dump_yaml, load_yaml, patch_hero_copy, patch_redesign
from .media import MediaTools, base_showcase_groups, default_media_tools, produce_media
from .memory import existing_variants, get_run, update_run
from .models import MediaPlan, PageBlueprint, RedesignChanges, SavedProposal
from .sources import creative_image_path, creative_video_path, youtube_ids
from .tile_prompt import reference_kind

__all__ = [
    "ApplyError",
    "DESCRIPTION_MAX",
    "FUNNEL_ALLOWED",
    "PREVIEW_BASE",
    "apply_run",
    "check_media_plan",
    "describe_variant",
    "hard_diff_site",
    "hard_diff_variant",
    "next_variant_name",
    "patch_identities",
    "patch_site",
]

Validate = Callable[[Path], None]
Emit = Callable[[str, str], None]

DESCRIPTION_MAX = 180
PREVIEW_BASE = "http://localhost:5173/pm"
LABELS = {
    "landing_rebuild": "landing-rebuild",
    "hero_copy": "hero-copy",
    "landing_redesign": "landing-redesign",
}
TITLES = {
    "landing_rebuild": "Landing rebuild experiment",
    "hero_copy": "Hero copy experiment",
    "landing_redesign": "Landing redesign experiment",
}


def next_variant_name(settings: Settings) -> str:
    taken = set(existing_variants(settings))
    index = 1
    while True:
        root = re.sub(r"_a[1-9][0-9]*$", "", settings.base_version)
        name = f"{root}_a{index}"
        if name not in taken and not (settings.pricing_lab_dir / "funnels" / name).exists():
            return name
        index += 1


def hyphenated(version: str) -> str:
    return version.replace("_", "-").replace("/", "-")


def patch_identities(funnel_path: Path, variant: str, experiment_type: str = "hero_copy") -> None:
    doc = load_yaml(funnel_path)
    slug = hyphenated(variant)
    doc["id"] = f"recraft-quiz-{slug}"
    doc["landingUser"] = f"pricing-lab-{slug}"
    doc["title"] = f"{TITLES.get(experiment_type, TITLES['hero_copy'])} {variant}"
    dump_yaml(funnel_path, doc)


def hard_diff_site(original: bytes, updated: Path, variant: str) -> None:
    old = YAML(typ="safe").load(original)
    new = YAML(typ="safe").load(updated.read_text(encoding="utf-8"))
    if old.get("default_version") != new.get("default_version"):
        raise ApplyError("hard-diff allowlist: default_version changed")
    if new.get("default_version") == variant:
        raise ApplyError("agent version cannot be default_version")
    old_pub = list(old.get("published_versions") or [])
    new_pub = list(new.get("published_versions") or [])
    if variant in old_pub:
        raise ApplyError(f"{variant} already published")
    if new_pub != old_pub + [variant]:
        raise ApplyError("hard-diff allowlist: published_versions must only append the variant")
    old_versions = dict(old.get("versions") or {})
    new_versions = dict(new.get("versions") or {})
    if any(old_versions.get(key) != new_versions.get(key) for key in old_versions):
        raise ApplyError("hard-diff allowlist: existing version descriptions changed")
    extra = set(new_versions) - set(old_versions)
    if extra != {variant}:
        raise ApplyError("hard-diff allowlist: versions map changed outside the new variant")
    other_old = {key: old[key] for key in old if key not in {"published_versions", "versions"}}
    other_new = {key: new[key] for key in new if key not in {"published_versions", "versions"}}
    if other_old != other_new:
        raise ApplyError(
            "hard-diff allowlist: site.yaml changed outside published_versions/versions"
        )


def describe_variant(variant: str, problem: str | None, experiment_type: str = "hero_copy") -> str:
    """One line for site.yaml `versions`: what the variant is, then the first sentence of why."""
    label = f"Agent {LABELS.get(experiment_type, LABELS['hero_copy'])} experiment {variant}"
    text = " ".join((problem or "").split())
    if not text:
        return label
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    line = f"{label}: {first}"
    if len(line) > DESCRIPTION_MAX:
        line = line[: DESCRIPTION_MAX - 1].rsplit(" ", 1)[0] + "…"
    return line


def _append_keeping_trailing_comment(seq: Any, value: str) -> None:
    """Append to a ruamel sequence so a comment that followed its last item still follows it."""
    last = len(seq) - 1
    comments = getattr(getattr(seq, "ca", None), "items", None)
    trailing = comments.pop(last, None) if comments is not None and last >= 0 else None
    seq.append(value)
    if trailing is not None:
        comments[len(seq) - 1] = trailing


def patch_site(
    site_path: Path, variant: str, problem: str | None, experiment_type: str = "hero_copy"
) -> None:
    doc = load_yaml(site_path)
    # Mutate the existing sequence: replacing it would drop the comments ruamel keeps on it.
    published = doc.get("published_versions")
    if published is None:
        doc["published_versions"] = [variant]
    elif variant not in published:
        _append_keeping_trailing_comment(published, variant)
    versions = doc.get("versions")
    if versions is None:
        doc["versions"] = {}
        versions = doc["versions"]
    versions[variant] = describe_variant(variant, problem, experiment_type)
    dump_yaml(site_path, doc)


def default_validate(funnels_dir: Path, settings: Settings) -> None:
    import os
    import subprocess

    completed = subprocess.run(
        ["npm", "run", "funnel:validate"],
        cwd=settings.pricing_lab_dir,
        env={**os.environ, "FUNNEL_DIRECTORY": str(funnels_dir)},
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "funnel:validate failed\n" + (completed.stdout or "") + (completed.stderr or "")
        )


def _tree_digest(root: Path) -> str:
    """Content hash of a directory tree, so apply can prove it left the base and _shared alone."""
    digest = hashlib.sha256()
    if not root.is_dir():
        return "absent"
    for rel, path in sorted(walk_files(root).items()):
        digest.update(rel.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def check_media_plan(plan: MediaPlan, proposal: SavedProposal, settings: Settings) -> None:
    from .web_assets import valid_asset

    for item in plan.sourced:
        if not isinstance(proposal.changes, PageBlueprint):
            raise ApplyError("Sourced media requires a campaign blueprint")
        asset = settings.web_assets.get(item.asset_id)
        if not asset or not valid_asset(asset, settings):
            raise ApplyError(
                "Sourced media must identify an inspected, unchanged local catalog asset"
            )
    """Refuse sources the model was not offered, before anything is downloaded or generated."""
    known = {item.creative_id for item in proposal.creative_evidence}
    clip = plan.hero_video
    if clip is not None:
        if clip.source == "youtube":
            if clip.source_id not in youtube_ids(settings):
                raise ApplyError(
                    f"media: youtube {clip.source_id} is not in the catalog offered by "
                    "get_media_sources"
                )
        else:
            if clip.source_id not in known:
                raise ApplyError(
                    f"media: creative {clip.source_id} is not among this proposal's ranked creatives"
                )
            if not creative_video_path(settings, clip.source_id).is_file():
                raise ApplyError(
                    f"media: creative {clip.source_id} has no local mp4 in growth-loop"
                )
    if not plan.showcase:
        return
    groups = base_showcase_groups(settings, proposal.base_version)
    reference_groups = dict(groups)
    if isinstance(proposal.changes, PageBlueprint):
        groups.update({b.id: (b.headline, [None] * 4) for b in proposal.changes.blocks})
    for item in plan.showcase:
        if item.group not in groups:
            raise ApplyError(f"media: showcase group {item.group!r} is not on the base landing")
        if item.slot >= len(groups[item.group][1]):
            raise ApplyError(f"media: showcase group {item.group!r} has no slot {item.slot}")
        for ref in item.references:
            kind = reference_kind(ref)
            if kind == "video":
                _, creative_id, seconds = ref.split(":")
                if (
                    creative_id not in known
                    or not creative_video_path(settings, creative_id).is_file()
                ):
                    raise ApplyError(
                        f"media: video reference {ref!r} is not among available selected creatives"
                    )
                from .video_analysis import probe_video

                duration, _ = probe_video(creative_video_path(settings, creative_id))
                if not 0 <= float(seconds) < duration:
                    raise ApplyError("media: video reference timestamp is outside the clip")
            elif kind == "creative":
                creative_id = ref.split(":", 1)[1]
                if creative_id not in known:
                    raise ApplyError(
                        f"media: reference {ref!r} is not among this proposal's ranked creatives"
                    )
                if not creative_image_path(settings, creative_id).is_file():
                    raise ApplyError(f"media: reference {ref!r} has no local jpg in growth-loop")
            elif kind == "tile":
                _, label, index_text = ref.split(":", 2)
                if label not in reference_groups or int(index_text) >= len(
                    reference_groups[label][1]
                ):
                    raise ApplyError(f"media: reference {ref!r} does not exist on the base landing")


def apply_run(
    settings: Settings,
    run_id: str,
    *,
    validate: Validate | None = None,
    media_tools: MediaTools | None = None,
    on_event: Emit | None = None,
    tile_variants: int | None = None,
    on_data: EmitData | None = None,
    variant_name: str | None = None,
) -> str:
    """Apply a proposed run as a new variant. `on_event` gets (stage, message) lines as before;
    `on_data` gets structured events (events.py) including every tile candidate and verdict."""
    try:
        return _apply_run(
            settings,
            run_id,
            validate=validate,
            media_tools=media_tools,
            on_event=on_event,
            tile_variants=tile_variants,
            on_data=on_data,
            variant_name=variant_name,
        )
    except Exception as error:
        emit_to(on_data, "apply_failed", {"runId": run_id, "error": str(error)})
        raise


def _apply_run(
    settings: Settings,
    run_id: str,
    *,
    validate: Validate | None,
    media_tools: MediaTools | None,
    on_event: Emit | None,
    tile_variants: int | None,
    on_data: EmitData | None,
    variant_name: str | None,
) -> str:
    row = get_run(settings, run_id)
    if row.status != "proposed":
        raise ApplyError(
            f"{run_id} is {row.status}"
            + (f" as {row.variant}" if row.variant else "")
            + "; a run is applied once. Propose again for another variant."
        )
    proposal = SavedProposal.model_validate(row.proposal)
    if proposal.decision != "experiment" or proposal.changes is None:
        raise ApplyError("no_experiment cannot be applied")
    current = landing_hash(settings.landing_path)
    if current != proposal.base_landing_hash:
        raise ApplyError(f"stale base hash: {settings.base_version} landing drifted since propose")
    rebuild = isinstance(proposal.changes, PageBlueprint)
    redesign = isinstance(proposal.changes, RedesignChanges)
    kind = "landing_rebuild" if rebuild else "landing_redesign" if redesign else "hero_copy"
    media_plan = proposal.changes.media if redesign or rebuild else None
    if media_plan is not None and media_plan.is_empty():
        media_plan = None
    if media_plan is not None:
        check_media_plan(media_plan, proposal, settings)

    variant = variant_name or next_variant_name(settings)
    from .workflow_catalog import safe_version

    safe_version(settings.pricing_lab_dir / "funnels", variant)
    dest = settings.pricing_lab_dir / "funnels" / variant
    if dest.exists():
        raise ApplyError(f"{variant} already exists; refuse overwrite")
    funnels = settings.pricing_lab_dir / "funnels"
    base_dir = funnels / settings.base_version
    shared_dir = funnels / "_shared"
    base_bytes = settings.landing_path.read_bytes()
    base_digest = _tree_digest(base_dir)
    shared_digest = _tree_digest(shared_dir)
    site_original = settings.site_path.read_bytes()
    old_default = str((YAML(typ="safe").load(site_original) or {}).get("default_version") or "")
    validator = validate or (lambda directory: default_validate(directory, settings))

    events: list[tuple[str, str]] = []

    def emit(stage: str, message: str) -> None:
        events.append((stage, message))
        if on_event is not None:
            on_event(stage, message)
        emit_to(on_data, "apply_stage", {"stage": stage, "message": message})

    emit_to(
        on_data,
        "apply_started",
        {"runId": run_id, "baseVersion": settings.base_version, "variant": variant, "kind": kind},
    )

    produced: ProducedMedia | None = None
    order: list[str] | None = None
    summary: list[str] = []
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=settings.tmp_dir) as tmp:
        tmp_funnels = Path(tmp) / "funnels"
        shutil.copytree(funnels, tmp_funnels)
        tmp_base = tmp_funnels / settings.base_version
        tmp_variant = tmp_funnels / variant
        shutil.copytree(tmp_base, tmp_variant)
        patch_identities(tmp_variant / "funnel.yaml", variant, kind)
        emit("copy", f"{settings.base_version} copied to {variant}")
        if media_plan is not None:
            tools = media_tools or default_media_tools(settings)
            produced = produce_media(
                media_plan,
                tmp_variant,
                settings,
                tools,
                emit,
                variants=tile_variants or settings.tile_variants,
                on_data=on_data,
            )
            emit("media", f"{len(produced.files.all)} media files staged")
        landing = tmp_variant / "steps" / "landing.yaml"
        if rebuild:
            from .blueprint import compose_document

            doc = compose_document(load_yaml(landing), proposal.changes, produced)
            dump_yaml(landing, doc)
            order = [row.get("id", row["component"]) for row in doc["props"]["sections"]]
            emit("patch", "New page composition: " + ", ".join(order))
        elif redesign:
            order = patch_redesign(landing, proposal.changes, produced)
            emit("patch", "sections: " + ", ".join(order))
        else:
            patch_hero_copy(landing, proposal.changes)
            emit("patch", "hero copy written")
        files = produced.files if produced is not None else ProducedFiles()
        summary = hard_diff_variant(tmp_base, tmp_variant, files)
        emit("diff", "allowlist ok: " + ("; ".join(summary) or "identity only"))
        emit("validate", "funnel:validate 1/2")
        validator(tmp_funnels)
        hard_diff_variant(tmp_base, tmp_variant, files)
        patch_site(tmp_funnels / "site.yaml", variant, proposal.problem, kind)
        hard_diff_site(site_original, tmp_funnels / "site.yaml", variant)
        emit("validate", "funnel:validate 2/2")
        validator(tmp_funnels)
        hard_diff_site(site_original, tmp_funnels / "site.yaml", variant)
        shutil.move(str(tmp_variant), str(dest))
        settings.site_path.write_bytes((tmp_funnels / "site.yaml").read_bytes())
        emit("publish", f"{variant} moved into place")

    if settings.landing_path.read_bytes() != base_bytes:
        raise RuntimeError(f"{settings.base_version} landing bytes changed during apply")
    if _tree_digest(base_dir) != base_digest:
        raise RuntimeError(f"{settings.base_version} files changed during apply")
    if _tree_digest(shared_dir) != shared_digest:
        raise RuntimeError("funnels/_shared changed during apply")
    update_run(settings, run_id, status="applied", variant=variant)
    emit_to(
        on_data,
        "apply_done",
        {"runId": run_id, "variant": variant, "previewUrl": f"{PREVIEW_BASE}/{variant}"},
    )

    lines = [
        f"Created {variant}",
        "",
        f"✓ proposal valid ({kind})",
        "✓ base hash matches",
        f"✓ {settings.base_version} unchanged",
        "✓ only allowed landing fields changed",
        f"✓ default_version remains {old_default or settings.base_version}",
        f"✓ {variant} added to published_versions",
        "✓ funnel validation passed",
    ]
    if produced is not None:
        lines.append(
            f"✓ {len(produced.files.all)} media files produced ({produced.cached_files} cached)"
        )
    for tile in produced.tiles if produced is not None else []:
        judge = tile.judge
        picked = judge.scores[judge.chosen] if judge and judge.scores else None
        score = f" ({picked.total:.1f})" if picked else ""
        chosen = f"#{judge.chosen}" if judge else "#0"
        lines.append(
            f"✓ tile {tile.spec.stem}: {tile.spec.plan.group} slot {tile.spec.plan.slot} {chosen}{score}"
        )
    if order is not None:
        lines.append("✓ sections: " + ", ".join(order))
    lines += [
        "",
        "Preview:",
        "",
        f"{PREVIEW_BASE}/{variant}",
        "(restart `npm run dev` if the variant 404s: Vite globs version folders at startup)",
        "",
        "Log:",
    ]
    lines += [f"• {stage}: {message}" for stage, message in events]
    return "\n".join(lines)
