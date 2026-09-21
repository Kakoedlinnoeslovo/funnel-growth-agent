"""Build a replayable event log from a run that already exists in memory, using only the
report, the caches and the lab on disk. No model is called. This is how a run applied before
the recorder existed (or applied from a plain terminal) becomes stage material."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from ..config import Settings
from ..creatives import get_top_creatives
from ..events import Event
from ..gemini import analyze_ranked
from ..landing import get_current_landing
from ..landing_diff import ApplyError
from ..landing_patch import compose_order
from ..media import (
    encoded_file_names,
    load_cached_tiles,
    tile_judged_data,
    tile_started_data,
)
from ..memory import get_previous_runs, get_run
from ..metrics import get_landing_cta_metrics, load_latest_reports
from ..models import RankedCreative, RedesignChanges, SavedProposal
from ..proposal_page import base_sections
from ..research import CachedResearch
from ..showcase_style import style_cache_path
from ..sources import creative_image_path
from .events import Recorder, recording_path, write_events

PREVIEW_BASE = "http://localhost:5173/pm"
PROPOSAL_KEYS = (
    "decision",
    "experimentType",
    "problem",
    "evidence",
    "hypothesis",
    "primaryMetric",
    "changes",
    "otherIdeas",
    "reason",
)


def _ticking_clock(step: float = 0.5):
    now = 0.0

    def clock() -> float:
        nonlocal now
        now += step
        return now

    return clock


def _ranked_for(settings: Settings, proposal: SavedProposal) -> list[RankedCreative]:
    """The ranked creatives the proposal cites: from the latest report when it still lists
    them, otherwise rebuilt from the evidence rows so the cards still have images."""
    try:
        weekly, _ = load_latest_reports(settings)
        ranked = {row.creative_id: row for row in get_top_creatives(weekly, settings)}
    except Exception:  # noqa: BLE001 - a missing or stale report is fine here
        ranked = {}
    out: list[RankedCreative] = []
    for item in proposal.creative_evidence:
        row = ranked.get(item.creative_id)
        if row is None:
            image = creative_image_path(settings, item.creative_id)
            row = RankedCreative(
                creative_id=item.creative_id,
                ad_name=item.creative_id,
                reason_selected=item.reason_selected,
                image_path=str(image) if image.is_file() else None,
            )
        out.append(row)
    return out


def _metrics(settings: Settings, proposal: SavedProposal) -> dict[str, Any]:
    try:
        return get_landing_cta_metrics(settings).model_dump(by_alias=True)
    except Exception:  # noqa: BLE001 - fall back to what the proposal recorded
        return {"baseline": proposal.baseline.model_dump(by_alias=True), "fresh": False}


def _research(settings: Settings) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not settings.research_dir.is_dir():
        return out
    for path in sorted(settings.research_dir.glob("*.json")):
        if path.name.startswith("showcase-"):
            continue
        try:
            cached = CachedResearch.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - skip files this version cannot read
            continue
        if cached.read is not None:
            out.append(json.loads(cached.model_dump_json(by_alias=True)))
    return out


def _showcase_style(settings: Settings) -> dict[str, Any] | None:
    path = style_cache_path(settings)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def synthesize_events(settings: Settings, run_id: str) -> list[Event]:
    row = get_run(settings, run_id)
    proposal = SavedProposal.model_validate(row.proposal)
    base = proposal.base_version
    settings = dataclasses.replace(settings, base_version=base)
    rec = Recorder(clock=_ticking_clock())
    rec(
        "propose_started",
        {"baseVersion": base, "model": settings.anthropic_model, "synthesized": True},
    )

    ranked = _ranked_for(settings, proposal)
    analyses = analyze_ranked(ranked, settings, call_model=False)
    landing = get_current_landing(settings) if settings.landing_path.is_file() else {"sections": []}
    metrics = _metrics(settings, proposal)
    rec(
        "context_ready",
        {
            "metrics": metrics,
            "ranked": [r.model_dump(by_alias=True) for r in ranked],
            "analyses": [a.model_dump(by_alias=True) for a in analyses],
            "landing": landing,
            "landingHash": proposal.base_landing_hash,
        },
    )

    def tool(name: str, arguments: dict[str, Any], result: Any) -> None:
        call_id = f"call-{rec.seq + 1}"
        rec("tool_call", {"id": call_id, "name": name, "input": arguments})
        rec("tool_result", {"id": call_id, "name": name, "result": result})

    tool("get_landing_cta_metrics", {}, metrics)
    tool("get_top_creatives", {}, {"creatives": [r.model_dump(by_alias=True) for r in ranked]})
    for analysis in analyses:
        tool(
            "get_creative_analysis",
            {"creative_id": analysis.creative_id},
            analysis.model_dump(by_alias=True),
        )
    tool("get_current_landing", {}, landing)
    experiment_type = proposal.experiment_type or "landing_redesign"
    earlier = [
        json.loads(r.model_dump_json(by_alias=True))
        for r in get_previous_runs(settings, experiment_type=experiment_type, limit=5)
        if r.run_id < run_id
    ]
    tool("get_previous_runs", {"experiment_type": experiment_type}, {"runs": earlier})
    style = _showcase_style(settings)
    if style is not None:
        tool("get_showcase_style", {}, style)
    for research in _research(settings):
        tool("research_landing", {"url": research.get("url")}, research)

    output = {
        key: value
        for key, value in proposal.model_dump(by_alias=True).items()
        if key in PROPOSAL_KEYS
    }
    output = {key: value for key, value in output.items() if value not in (None, [])}
    rec("proposal", {"output": output})
    rec("proposal_saved", {"runId": run_id, "status": "proposed", "decision": proposal.decision})

    if row.status not in {"applied", "deployed", "measuring", "evaluated"} or not row.variant:
        return rec.events

    variant = row.variant
    rec.phase = "apply"
    kind = experiment_type
    rec("apply_started", {"runId": run_id, "baseVersion": base, "variant": variant, "kind": kind})

    def stage(name: str, message: str, **extra: Any) -> None:
        rec("apply_stage", {"stage": name, "message": message, **extra})

    stage("copy", f"{base} copied to {variant}")
    changes = proposal.changes
    media = changes.media if isinstance(changes, RedesignChanges) else None
    files = 0
    if media is not None and media.hero_video is not None:
        clip = media.hero_video
        encoded = settings.media_cache_dir / "encoded" / clip.stem
        poster = encoded / f"{clip.stem}-poster-1280.webp"
        stage(
            "media",
            f"clip {clip.stem}: cached",
            stem=clip.stem,
            posterPath=str(poster) if poster.is_file() else None,
        )
        files += len(encoded_file_names(clip.stem))
    if media is not None and media.showcase:
        try:
            results = load_cached_tiles(media, settings, base)
        except ApplyError:
            results = []
        for result in results:
            rec("tile_started", tile_started_data(result.spec))
            for index, path in enumerate(result.candidates):
                rec("tile_candidate", {"stem": result.spec.stem, "index": index, "path": str(path)})
            if result.judge is not None:
                rec("tile_judged", tile_judged_data(result.judge))
            files += 2
    if media is not None:
        stage("media", f"{files} media files staged")
    ids, _sections = base_sections(settings, base)
    if isinstance(changes, RedesignChanges) and ids:
        try:
            order = compose_order(ids, changes.composition)
        except ApplyError:
            order = ids
        stage("patch", "sections: " + ", ".join(order))
    else:
        stage("patch", "hero copy written")
    stage("diff", "allowlist ok")
    stage("validate", "funnel:validate 1/2")
    stage("validate", "funnel:validate 2/2")
    stage("publish", f"{variant} moved into place")
    rec(
        "apply_done",
        {"runId": run_id, "variant": variant, "previewUrl": f"{PREVIEW_BASE}/{variant}"},
    )
    return rec.events


def synthesize_recording(settings: Settings, run_id: str) -> Path:
    events = synthesize_events(settings, run_id)
    return write_events(recording_path(settings, run_id), events)
