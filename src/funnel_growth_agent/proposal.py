"""Build the saved proposal envelope and run propose."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol

from pydantic import ValidationError

from .agent import ToolLoop, run_tool_loop
from .config import Settings
from .creatives import get_top_creatives
from .events import EmitData, emit_to
from .gemini import analyze_ranked
from .landing import get_current_landing, landing_hash
from .memory import append_run, list_runs
from .metrics import get_landing_cta_metrics, load_latest_reports
from .models import (
    CachedCreativeAnalysis,
    CreativeEvidence,
    LandingProposal,
    MemoryRow,
    MetricsSlice,
    NoExperiment,
    RankedCreative,
    SavedProposal,
    parse_model_output,
)


class Completer(Protocol):
    def complete(self, messages: list[dict[str, Any]], system: str, execute: Any) -> Any: ...


def next_run_id(settings: Settings) -> str:
    stamp = settings.clock().strftime("%Y-%m-%dT%H%M")
    prefix = f"{stamp}-{settings.base_version.replace('/', '-')}-"
    count = sum(1 for row in list_runs(settings) if row.run_id.startswith(prefix))
    return f"{prefix}{count + 1:03d}"


def _evidence(
    ranked: list[Any],
    analyses: list[Any],
) -> list[CreativeEvidence]:
    by_id = {item.creative_id: item.analysis for item in analyses}
    out: list[CreativeEvidence] = []
    for creative in ranked:
        analysis = by_id.get(creative.creative_id)
        out.append(
            CreativeEvidence(
                creative_id=creative.creative_id,
                reason_selected=creative.reason_selected,
                primary_promise=analysis.primary_promise if analysis else None,
                cta_intent=analysis.cta_intent if analysis else None,
            )
        )
    return out


def envelope_from_output(
    settings: Settings,
    output: LandingProposal | NoExperiment,
    *,
    baseline: Any,
    landing_sha: str,
    creative_evidence: list[CreativeEvidence],
) -> SavedProposal:
    run_id = next_run_id(settings)
    if isinstance(output, NoExperiment):
        return SavedProposal(
            run_id=run_id,
            base_version=settings.base_version,
            base_landing_hash=landing_sha,
            baseline=baseline,
            creative_evidence=creative_evidence,
            decision="no_experiment",
            reason=output.reason,
            other_ideas=output.other_ideas,
        )
    return SavedProposal(
        run_id=run_id,
        base_version=settings.base_version,
        base_landing_hash=landing_sha,
        baseline=baseline,
        creative_evidence=creative_evidence,
        decision="experiment",
        experiment_type=output.experiment_type,
        problem=output.problem,
        evidence=output.evidence,
        hypothesis=output.hypothesis,
        primary_metric=output.primary_metric,
        changes=output.changes,
        interpreted_brief=output.interpreted_brief,
        competitor_adaptations=output.competitor_adaptations,
        other_ideas=output.other_ideas,
    )


def _coerce_output(raw: Any) -> LandingProposal | NoExperiment:
    if isinstance(raw, (LandingProposal, NoExperiment)):
        return raw
    if isinstance(raw, str):
        return parse_model_output(__import__("json").loads(raw))
    return parse_model_output(raw)


def propose(
    settings: Settings,
    *,
    model: Completer | None = None,
    refresh_creatives: bool = False,
    browser: Any = None,
    reader: Any = None,
    style_reader: Any = None,
    on_event: EmitData | None = None,
    creatives: list[RankedCreative] | None = None,
    analyses: list[CachedCreativeAnalysis] | None = None,
    metrics: MetricsSlice | None = None,
    brief: str | None = None,
    research_records: list[dict] | None = None,
) -> SavedProposal:
    """Propose once. `on_event` (kind, data) sees the pipeline as it runs; see events.py."""
    try:
        return _propose(
            settings,
            model=model,
            refresh_creatives=refresh_creatives,
            browser=browser,
            reader=reader,
            style_reader=style_reader,
            on_event=on_event,
            creatives=creatives,
            analyses=analyses,
            metrics=metrics,
            brief=brief,
            research_records=research_records,
        )
    except Exception as error:
        emit_to(on_event, "propose_failed", {"error": str(error)})
        raise


def _propose(
    settings: Settings,
    *,
    model: Completer | None,
    refresh_creatives: bool,
    browser: Any,
    reader: Any,
    style_reader: Any,
    on_event: EmitData | None,
    creatives: list[RankedCreative] | None,
    analyses: list[CachedCreativeAnalysis] | None,
    metrics: MetricsSlice | None,
    brief: str | None,
    research_records: list[dict] | None = None,
) -> SavedProposal:
    emit_to(
        on_event,
        "propose_started",
        {"baseVersion": settings.base_version, "model": settings.anthropic_model},
    )
    metrics = metrics or get_landing_cta_metrics(settings)
    if creatives is None:
        weekly, _daily = load_latest_reports(settings)
        ranked = get_top_creatives(weekly, settings)
    else:
        ranked = creatives
    analyses = (
        analyses
        if analyses is not None
        else analyze_ranked(
            ranked,
            settings,
            refresh=refresh_creatives,
            call_model=bool(settings.gemini_api_key),
        )
    )
    landing_sha = landing_hash(settings.landing_path)
    emit_to(
        on_event,
        "context_ready",
        {
            "metrics": metrics.model_dump(by_alias=True),
            "ranked": [row.model_dump(by_alias=True) for row in ranked],
            "analyses": [row.model_dump(by_alias=True) for row in analyses],
            "landing": get_current_landing(settings),
            "landingHash": landing_sha,
        },
    )
    try:
        brief_data = json.loads(brief or "{}")
        policy = (
            {
                key: brief_data[key]
                for key in ("changeLevel", "previousProposal", "recipe")
                if key in brief_data
            }
            if isinstance(brief_data, dict)
            else {}
        )
    except (ValueError, TypeError):
        policy = {}
    tools = ToolLoop(
        settings,
        ranked=ranked,
        analyses=analyses,
        metrics=metrics,
        browser=browser,
        style_reader=style_reader,
        reader=reader,
        on_event=on_event,
        selected=creatives is not None,
        policy=policy,
        research_records=research_records,
    )
    if model is None:
        raw = run_tool_loop(settings, tools, brief=brief)
    else:
        raw = model.complete(
            [
                {
                    "role": "user",
                    "content": brief or "Propose one landing redesign experiment or no_experiment.",
                }
            ],
            tools.system_prompt(),
            tools.execute,
        )
    try:
        output = _coerce_output(raw)
        tools.validate_output(output)
    except (ValidationError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid model output; no files were written: {error}") from error
    if model is not None:
        # The scripted path bypasses run_tool_loop, which is where the live loop emits this.
        emit_to(on_event, "proposal", {"output": output.model_dump(by_alias=True)})
    saved = envelope_from_output(
        settings,
        output,
        baseline=metrics.baseline,
        landing_sha=landing_sha,
        creative_evidence=_evidence(ranked, analyses),
    )
    append_run(
        settings,
        MemoryRow(
            run_id=saved.run_id,
            created_at=datetime.now().isoformat(timespec="seconds"),
            status="proposed",
            base=settings.base_version,
            variant=None,
            proposal=saved.model_dump(by_alias=True),
            deployed_at=None,
            evaluation=None,
            learning=None,
        ),
    )
    emit_to(
        on_event,
        "proposal_saved",
        {"runId": saved.run_id, "status": "proposed", "decision": saved.decision},
    )
    return saved
