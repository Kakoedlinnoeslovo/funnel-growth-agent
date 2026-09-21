"""One self-contained HTML page for a saved run: the evidence, the hypothesis, every change as
before → after against the base landing, the clip plan and the showcase tiles with their
candidates and judge scores. Reads memory and caches only; never calls a model."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .config import Settings
from .landing import resolve_section_ids
from .landing_diff import ApplyError
from .landing_patch import compose_order
from .media import load_cached_tiles
from .memory import get_run
from .models import (
    CompositionChanges,
    HeroCopyChanges,
    HeroVideoPlan,
    MediaPlan,
    MemoryRow,
    RedesignChanges,
    SavedProposal,
)
from .page_style import document, esc, rel_link, swatch
from .sources import creative_image_path
from .tile_sheet import render_tile_card

PREVIEW_BASE = "http://localhost:5173/pm"
COPY_FIELDS = (
    ("headline", "headline"),
    ("subhead", "subhead"),
    ("ctaLabel", "cta_label"),
    ("reassurance", "reassurance"),
)
SECTION_FOR = {
    "hero": "kittl-hero",
    "videoCta": "video-cta",
    "finalCta": "final-cta",
    "inlineCta": "inline-cta",
}
SECTION_TITLES = {
    "hero": "Hero",
    "videoCta": "Video CTA",
    "finalCta": "Final CTA",
    "inlineCta": "Inline CTA (every one on the page)",
}

_yaml = YAML(typ="safe")


# ---------------------------------------------------------------------------------------
# Base landing


def base_sections(
    settings: Settings, base_version: str
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Section ids in page order and each section's raw mapping, from the base landing."""
    path = settings.pricing_lab_dir / "funnels" / base_version / "steps" / "landing.yaml"
    if not path.is_file():
        return [], {}
    doc = _yaml.load(path.read_text(encoding="utf-8")) or {}
    raw = list(((doc.get("props") or {}).get("sections")) or [])
    ids = resolve_section_ids(raw)
    return ids, {sid: section for sid, section in zip(ids, raw)}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


# ---------------------------------------------------------------------------------------
# Changes → rows


def copy_rows(
    changes: Any, sections: dict[str, dict[str, Any]]
) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """[(section title, [(field, before, after)])] for every field the proposal sets to a new
    value. Fields equal to the base are dropped: they are not a change."""
    parts: list[tuple[str, Any]] = []
    if isinstance(changes, RedesignChanges):
        copy = changes.copy_changes
        if copy is not None:
            parts = [
                ("hero", copy.hero),
                ("videoCta", copy.video_cta),
                ("finalCta", copy.final_cta),
                ("inlineCta", copy.inline_cta),
            ]
    elif isinstance(changes, HeroCopyChanges):
        parts = [
            ("hero", changes),
            ("videoCta", changes.video_cta),
            ("finalCta", changes.final_cta),
        ]
    out: list[tuple[str, list[tuple[str, str, str]]]] = []
    for key, part in parts:
        if part is None:
            continue
        base = sections.get(SECTION_FOR[key]) or {}
        rows: list[tuple[str, str, str]] = []
        for field, attr in COPY_FIELDS:
            if not hasattr(part, attr):
                continue
            after = getattr(part, attr)
            if after is None:
                continue
            before = _text(base.get(field))
            if _text(after) != before:
                rows.append((field, before, _text(after)))
        if rows:
            out.append((SECTION_TITLES[key], rows))
    return out


def layout_rows(changes: Any, hero: dict[str, Any]) -> list[tuple[str, str, str]]:
    if not isinstance(changes, RedesignChanges) or changes.layout is None:
        return []
    rows: list[tuple[str, str, str]] = []
    if changes.layout.layout is not None and changes.layout.layout != hero.get("layout"):
        rows.append(("layout", _text(hero.get("layout")), changes.layout.layout))
    if changes.layout.sticky_cta is not None and changes.layout.sticky_cta != hero.get("stickyCta"):
        rows.append(("stickyCta", _text(hero.get("stickyCta")), _text(changes.layout.sticky_cta)))
    return rows


def composition_chips(composition: CompositionChanges | None, ids: list[str]) -> str:
    """The new section order as chips: moved ones outlined, omitted ones struck at the end."""
    if composition is None or composition.is_empty() or not ids:
        return ""
    try:
        new_order = compose_order(ids, composition)
    except ApplyError as error:
        return f"<div class='muted'>{esc(error)}</div>"
    omitted = set(composition.omit or [])
    survivors = [sid for sid in ids if sid not in omitted]
    chips: list[str] = []
    for index, sid in enumerate(new_order):
        moved = survivors.index(sid) != index if sid in survivors else False
        chips.append(f"<span class='chip{' moved' if moved else ''}'>{esc(sid)}</span>")
    chips += [f"<span class='chip omit'>{esc(sid)}</span>" for sid in ids if sid in omitted]
    return "<div class='chips'>" + "".join(chips) + "</div>"


# ---------------------------------------------------------------------------------------
# HTML pieces


def _fields_html(rows: list[tuple[str, str, str]], *, lit: bool = True) -> str:
    out: list[str] = []
    for field, before, after in rows:
        before_html = f"<div class='before'>{esc(before)}</div>" if before else ""
        out.append(
            f"<div class='field'><div class='k'>{esc(field)}</div>"
            f"<div>{before_html}<div class='after{' lit' if lit else ''}'>{esc(after)}</div></div></div>"
        )
    return "".join(out)


def _status_html(row: MemoryRow) -> str:
    if row.status == "applied" and row.variant:
        return f"<span class='badge'>applied · {esc(row.variant)}</span>"
    return f"<span class='pill'>{esc(row.status)}</span>"


def _header_html(row: MemoryRow, proposal: SavedProposal) -> str:
    right: list[str] = [_status_html(row)]
    if row.variant:
        url = f"{PREVIEW_BASE}/{row.variant}"
        right.append(f"<div style='margin-top:8px'><a href='{esc(url)}'>{esc(url)}</a></div>")
    else:
        right.append("<div class='muted' style='margin-top:8px'>No files were modified</div>")
    kind = proposal.experiment_type or proposal.decision
    return (
        "<div class='head'><div>"
        f"<h1>{esc(row.run_id)}</h1>"
        f"<div class='muted'>{esc(kind)} · base {esc(proposal.base_version)} · {esc(row.created_at)}</div>"
        "</div><div style='text-align:right'>" + "".join(right) + "</div></div>"
    )


def _baseline_html(proposal: SavedProposal) -> str:
    b = proposal.baseline
    proxy = " · proxy from an older version" if b.is_proxy else ""
    period = f" · {b.period}" if b.period else ""
    return (
        "<div class='card'><h3>Baseline</h3>"
        f"<div class='kpi'>{b.cta_rate:.1%}<small>landing → CTA</small></div>"
        f"<div class='muted' style='margin-top:6px'>{b.landing_people:,} landing · "
        f"{b.cta_people:,} CTA clicks · {esc(b.version)}{esc(proxy)}{esc(period)}</div></div>"
    )


def _creatives_html(proposal: SavedProposal, settings: Settings, base_dir: Path) -> str:
    if not proposal.creative_evidence:
        return ""
    out = ["<div class='card'><h3>Winning creatives</h3><div class='grid'>"]
    for item in proposal.creative_evidence:
        image = creative_image_path(settings, item.creative_id)
        thumb = (
            f"<img class='thumb' src='{esc(rel_link(image, base_dir))}'>"
            if image.is_file()
            else "<div class='thumb'></div>"
        )
        promise = item.primary_promise or item.cta_intent or ""
        out.append(
            f"<div class='creative'>{thumb}<div class='body'>"
            f"<div>{esc(promise)}</div>"
            f"<div class='muted'>{esc(item.reason_selected)} · {esc(item.creative_id)}</div>"
            "</div></div>"
        )
    out.append("</div></div>")
    return "".join(out)


def _evidence_html(proposal: SavedProposal) -> str:
    out = [f"<div class='card'><h3>Problem</h3><p style='margin:0'>{esc(proposal.problem)}</p>"]
    if proposal.evidence:
        out.append("<details style='margin-top:10px'><summary>Evidence</summary><ul>")
        out += [f"<li>{esc(line)}</li>" for line in proposal.evidence]
        out.append("</ul></details>")
    out.append("</div>")
    metric = (
        f"<span class='pill'>{esc(proposal.primary_metric)}</span>"
        if proposal.primary_metric
        else ""
    )
    out.append(
        f"<div class='card'><h3>Hypothesis {metric}</h3><p style='margin:0'>{esc(proposal.hypothesis)}</p></div>"
    )
    return "".join(out)


def _clip_html(
    plan: HeroVideoPlan, settings: Settings, base_dir: Path, hero: dict[str, Any]
) -> str:
    poster = settings.media_cache_dir / "encoded" / plan.stem / f"{plan.stem}-poster-1280.webp"
    if plan.source == "youtube":
        source = f"<a href='https://www.youtube.com/watch?v={esc(plan.video_id)}'>youtube {esc(plan.video_id)}</a>"
    else:
        source = f"creative {esc(plan.creative_id)}"
    before = (
        ((hero.get("video") or {}).get("label")) if isinstance(hero.get("video"), dict) else None
    )
    rows = [("clip", _text(before), plan.label)]
    out = ["<div class='card'><h3>Hero clip</h3>"]
    if poster.is_file():
        out.append(f"<img class='poster' src='{esc(rel_link(poster, base_dir))}'>")
    else:
        out.append("<div class='muted'>Not produced yet: apply encodes the clip.</div>")
    out.append(
        f"<div class='muted' style='margin:8px 0'>{source} · {plan.start:g}s + {plan.duration:g}s · {esc(plan.aspect)}</div>"
    )
    out.append(_fields_html(rows))
    out.append("</div>")
    return "".join(out)


def _tiles_html(
    plan: MediaPlan, settings: Settings, base_version: str, base_dir: Path, run_id: str
) -> str:
    if not plan.showcase:
        return ""
    found: dict[tuple[str, int], Any] = {}
    for result in load_cached_tiles(plan, settings, base_version):
        found[(result.spec.plan.group, result.spec.plan.slot)] = result
    out = [f"<h2>Showcase tiles <span class='muted'>{len(plan.showcase)}</span></h2>"]
    for item in plan.showcase:
        result = found.get((item.group, item.slot))
        if result is not None:
            out.append(render_tile_card(result, base_dir, brief_first=True, run_id=run_id))
            continue
        out.append(
            "<div class='card'>"
            f"<h2>{esc(item.group)} · slot {item.slot} <span class='pill'>{esc(item.medium or 'prompt')}</span>"
            f" <span class='pill'>{esc(item.tile_model)}</span></h2>"
            f"<p style='margin:8px 0'>{esc(item.brief or item.prompt)}</p>"
            + (
                "<div>" + " ".join(swatch(c) for c in item.palette) + "</div>"
                if item.palette
                else ""
            )
            + (f"<div class='muted'>on {esc(item.background)}</div>" if item.background else "")
            + (
                "<div class='muted' style='margin-top:6px'>refs: "
                + ", ".join(esc(ref) for ref in item.references)
                + "</div>"
                if item.references
                else ""
            )
            + f"<div class='muted' style='margin-top:10px'>Not generated yet. Run funnel-growth tiles {esc(run_id)}.</div>"
            "</div>"
        )
    return "".join(out)


def _ideas_html(proposal: SavedProposal) -> str:
    if not proposal.other_ideas:
        return ""
    items = "".join(f"<li>{esc(idea)}</li>" for idea in proposal.other_ideas)
    return (
        "<details class='card'><summary>Alternative ideas "
        f"({len(proposal.other_ideas)})</summary><ol>{items}</ol></details>"
    )


def _evaluation_html(row: MemoryRow) -> str:
    if row.evaluation is None and not row.learning:
        return ""
    out = ["<div class='card'><h3>Evaluation</h3>"]
    if row.evaluation is not None:
        e = row.evaluation
        rate = f"{e.cta_rate:.1%}" if e.cta_rate is not None else "n/a"
        out.append(
            f"<div>{esc(e.result)} · {rate} on {e.landing_people:,} landing · causal: no</div>"
        )
    if row.learning:
        out.append(f"<div class='muted' style='margin-top:6px'>{esc(row.learning)}</div>")
    out.append("</div>")
    return "".join(out)


# ---------------------------------------------------------------------------------------
# Page


def render_page(settings: Settings, row: MemoryRow, *, base_dir: Path) -> str:
    proposal = SavedProposal.model_validate(row.proposal)
    body: list[str] = [_header_html(row, proposal)]
    if proposal.decision == "no_experiment":
        body.append(
            f"<div class='card'><h3>No experiment</h3><p style='margin:0'>{esc(proposal.reason)}</p></div>"
        )
        body.append(_baseline_html(proposal))
        body.append(_ideas_html(proposal))
        return document(row.run_id, "\n".join(body))

    ids, sections = base_sections(settings, proposal.base_version)
    hero = sections.get("kittl-hero") or {}
    body.append(
        "<div class='grid'>"
        + _baseline_html(proposal)
        + _creatives_html(proposal, settings, base_dir)
        + "</div>"
    )
    body.append(_evidence_html(proposal))

    changes = proposal.changes
    body.append("<h2>Changes</h2>")
    copy = copy_rows(changes, sections)
    if copy:
        body.append("<div class='card'><h3>Copy</h3>")
        for title, rows in copy:
            body.append(f"<div class='muted' style='margin-top:10px'>{esc(title)}</div>")
            body.append(_fields_html(rows))
        body.append("</div>")
    layout = layout_rows(changes, hero)
    composition = composition_chips(
        changes.composition if isinstance(changes, RedesignChanges) else None, ids
    )
    if layout or composition:
        body.append("<div class='card'><h3>Layout and composition</h3>")
        body.append(_fields_html(layout))
        if composition:
            body.append("<div style='margin-top:10px'>" + composition + "</div>")
            body.append(
                "<div class='muted' style='margin-top:6px'>Outlined = moved, struck = omitted; the hero always stays first.</div>"
            )
        body.append("</div>")
    media = changes.media if isinstance(changes, RedesignChanges) else None
    if media is not None and media.hero_video is not None:
        body.append(_clip_html(media.hero_video, settings, base_dir, hero))
    if media is not None:
        body.append(_tiles_html(media, settings, proposal.base_version, base_dir, row.run_id))
    body.append(_evaluation_html(row))
    body.append(_ideas_html(proposal))
    return document(row.run_id, "\n".join(body))


def pages_dir(settings: Settings) -> Path:
    return settings.media_cache_dir / "pages"


def write_page(settings: Settings, run_id: str) -> Path:
    row = get_run(settings, run_id)
    out_dir = pages_dir(settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.html"
    path.write_text(render_page(settings, row, base_dir=out_dir), encoding="utf-8")
    return path
