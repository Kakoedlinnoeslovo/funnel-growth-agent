"""Build the saved proposal envelope and run propose."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import ValidationError

from .agent import ToolLoop, run_tool_loop
from .config import Settings
from .creatives import get_top_creatives
from .gemini import analyze_ranked
from .landing import landing_hash
from .memory import append_run, list_runs
from .metrics import get_landing_cta_metrics, load_latest_reports
from .models import (
    CreativeEvidence,
    LandingProposal,
    MemoryRow,
    NoExperiment,
    SavedProposal,
    parse_model_output,
)


class Completer(Protocol):
    def complete(self, messages: list[dict[str, Any]], system: str, execute: Any) -> Any: ...


def next_run_id(settings: Settings) -> str:
    stamp = settings.clock().strftime("%Y-%m-%dT%H%M")
    prefix = f"{stamp}-{settings.base_version}-"
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
) -> SavedProposal:
    metrics = get_landing_cta_metrics(settings)
    weekly, _daily = load_latest_reports(settings)
    ranked = get_top_creatives(weekly, settings)
    analyses = analyze_ranked(
        ranked,
        settings,
        refresh=refresh_creatives,
        call_model=bool(settings.gemini_api_key),
    )
    landing_sha = landing_hash(settings.landing_path)
    tools = ToolLoop(settings, ranked=ranked, analyses=analyses, metrics=metrics)
    if model is None:
        raw = run_tool_loop(settings, tools)
    else:
        raw = model.complete(
            [{"role": "user", "content": "Propose one landing CTA experiment or no_experiment."}],
            tools.system_prompt(),
            tools.execute,
        )
    try:
        output = _coerce_output(raw)
    except (ValidationError, TypeError, ValueError) as error:
        raise ValueError(f"Invalid model output; no files were written: {error}") from error
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
    return saved
