"""Transactional apply: temp copy, allowlist, two validates, atomic publish."""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ruamel.yaml import YAML

from .config import Settings
from .landing import landing_hash
from .memory import existing_variants, get_run, update_run
from .models import HeroCopyChanges, SavedProposal, SectionCopy

Validate = Callable[[Path], None]

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096
# Match the pricing-lab's hand-written style so a variant diffs only where copy changed.
_yaml.indent(mapping=2, sequence=4, offset=2)

DESCRIPTION_MAX = 180

HERO_TEXT = {"headline", "subhead", "ctaLabel", "reassurance"}
SECTION_COMPONENTS = {"kittl-hero", "video-cta", "final-cta"}
FUNNEL_ALLOWED = {"id", "landingUser", "title"}


class ApplyError(ValueError):
    pass


def next_variant_name(settings: Settings) -> str:
    taken = set(existing_variants(settings))
    index = 1
    while True:
        name = f"{settings.base_version}_a{index}"
        if name not in taken and not (settings.pricing_lab_dir / "funnels" / name).exists():
            return name
        index += 1


def hyphenated(version: str) -> str:
    return version.replace("_", "-")


def _load(path: Path) -> Any:
    return _yaml.load(path.read_text(encoding="utf-8"))


def _dump(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        _yaml.dump(data, handle)


def patch_identities(funnel_path: Path, variant: str) -> None:
    doc = _load(funnel_path)
    slug = hyphenated(variant)
    doc["id"] = f"recraft-quiz-{slug}"
    doc["landingUser"] = f"pricing-lab-{slug}"
    doc["title"] = f"Hero copy experiment {variant}"
    _dump(funnel_path, doc)


def _apply_copy(section: dict[str, Any], copy: SectionCopy | HeroCopyChanges) -> None:
    if copy.headline is not None:
        section["headline"] = copy.headline
    if copy.subhead is not None:
        section["subhead"] = copy.subhead
    if copy.cta_label is not None:
        section["ctaLabel"] = copy.cta_label
    if copy.reassurance is not None:
        section["reassurance"] = copy.reassurance


def patch_landing(landing_path: Path, changes: HeroCopyChanges) -> None:
    doc = _load(landing_path)
    sections = ((doc.get("props") or {}).get("sections")) or []
    for section in sections:
        component = section.get("component")
        if component == "kittl-hero":
            _apply_copy(section, changes)
        elif component == "video-cta" and changes.video_cta:
            _apply_copy(section, changes.video_cta)
        elif component == "final-cta" and changes.final_cta:
            _apply_copy(section, changes.final_cta)
    _dump(landing_path, doc)


def _walk_files(root: Path) -> dict[str, Path]:
    return {str(path.relative_to(root)): path for path in root.rglob("*") if path.is_file()}


def _changed_keys(old: Any, new: Any, prefix: str = "") -> list[str]:
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
                out.extend(_changed_keys(old[key], new[key], path))
        return out
    if isinstance(old, list):
        if len(old) != len(new):
            return [prefix or "$"]
        out: list[str] = []
        for index, (left, right) in enumerate(zip(old, new)):
            out.extend(_changed_keys(left, right, f"{prefix}[{index}]"))
        return out
    return [prefix or "$"]


def _landing_change_allowed(path: str, old_doc: dict[str, Any]) -> bool:
    if not path.startswith("props.sections["):
        return False
    close = path.find("]")
    index = int(path[len("props.sections[") : close])
    remainder = path[close + 2 :] if path[close + 1 : close + 2] == "." else ""
    sections = ((old_doc.get("props") or {}).get("sections")) or []
    if index >= len(sections):
        return False
    component = sections[index].get("component")
    return component in SECTION_COMPONENTS and remainder in HERO_TEXT


def hard_diff_variant(base_dir: Path, variant_dir: Path) -> None:
    base_files = _walk_files(base_dir)
    variant_files = _walk_files(variant_dir)
    if set(base_files) != set(variant_files):
        raise ApplyError("hard-diff allowlist: variant file set differs from the base version")
    for rel, base_path in base_files.items():
        variant_path = variant_files[rel]
        if base_path.read_bytes() == variant_path.read_bytes():
            continue
        if rel == "funnel.yaml":
            changed = _changed_keys(_load(base_path), _load(variant_path))
            if any(key not in FUNNEL_ALLOWED for key in changed):
                raise ApplyError(f"hard-diff allowlist: funnel.yaml changed {changed}")
            continue
        if rel == "steps/landing.yaml":
            old_doc = _load(base_path)
            new_doc = _load(variant_path)
            changed = _changed_keys(old_doc, new_doc)
            if any(not _landing_change_allowed(key, old_doc) for key in changed):
                raise ApplyError(f"hard-diff allowlist: landing.yaml changed {changed}")
            continue
        raise ApplyError(f"hard-diff allowlist: unexpected change in {rel}")


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
        raise ApplyError("hard-diff allowlist: site.yaml changed outside published_versions/versions")


def describe_variant(variant: str, problem: str | None) -> str:
    """One line for site.yaml `versions`: what the variant is, then the first sentence of why."""
    label = f"Agent hero-copy experiment {variant}"
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


def patch_site(site_path: Path, variant: str, problem: str | None) -> None:
    doc = _load(site_path)
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
    versions[variant] = describe_variant(variant, problem)
    _dump(site_path, doc)


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


def apply_run(
    settings: Settings,
    run_id: str,
    *,
    validate: Validate | None = None,
) -> str:
    row = get_run(settings, run_id)
    proposal = SavedProposal.model_validate(row.proposal)
    if proposal.decision != "experiment" or proposal.changes is None:
        raise ApplyError("no_experiment cannot be applied")
    current = landing_hash(settings.landing_path)
    if current != proposal.base_landing_hash:
        raise ApplyError(
            f"stale base hash: {settings.base_version} landing drifted since propose"
        )
    variant = next_variant_name(settings)
    dest = settings.pricing_lab_dir / "funnels" / variant
    if dest.exists():
        raise ApplyError(f"{variant} already exists; refuse overwrite")
    funnels = settings.pricing_lab_dir / "funnels"
    base_bytes = settings.landing_path.read_bytes()
    site_original = settings.site_path.read_bytes()
    validator = validate or (lambda directory: default_validate(directory, settings))

    with TemporaryDirectory() as tmp:
        tmp_funnels = Path(tmp) / "funnels"
        shutil.copytree(funnels, tmp_funnels)
        tmp_variant = tmp_funnels / variant
        shutil.copytree(tmp_funnels / settings.base_version, tmp_variant)
        patch_identities(tmp_variant / "funnel.yaml", variant)
        patch_landing(tmp_variant / "steps" / "landing.yaml", proposal.changes)
        hard_diff_variant(tmp_funnels / settings.base_version, tmp_variant)
        validator(tmp_funnels)
        hard_diff_variant(tmp_funnels / settings.base_version, tmp_variant)
        patch_site(tmp_funnels / "site.yaml", variant, proposal.problem)
        hard_diff_site(site_original, tmp_funnels / "site.yaml", variant)
        validator(tmp_funnels)
        hard_diff_site(site_original, tmp_funnels / "site.yaml", variant)
        shutil.move(str(tmp_variant), str(dest))
        settings.site_path.write_bytes((tmp_funnels / "site.yaml").read_bytes())

    if settings.landing_path.read_bytes() != base_bytes:
        raise RuntimeError(f"{settings.base_version} landing bytes changed during apply")
    update_run(settings, run_id, status="applied", variant=variant)
    return (
        f"Created {variant}\n\n"
        "✓ proposal valid\n"
        "✓ base hash matches\n"
        f"✓ {settings.base_version} unchanged\n"
        "✓ only allowed landing fields changed\n"
        f"✓ default_version remains {settings.base_version}\n"
        f"✓ {variant} added to published_versions\n"
        "✓ funnel validation passed\n\n"
        "Preview:\n\n"
        f"http://localhost:5173/pm/{variant}"
    )
