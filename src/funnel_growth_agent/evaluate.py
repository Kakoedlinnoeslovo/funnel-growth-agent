"""Directional evaluate after real v7_aN traffic. Never causal."""

from __future__ import annotations

from typing import Any

from .config import Settings
from .memory import get_run, update_run
from .metrics import baseline_from_report, load_latest_reports
from .models import Evaluation, SavedProposal

FLAT_ABS = 0.005


def _flow_for_variant(report: dict[str, Any], variant: str, funnel_id: str | None) -> dict[str, Any] | None:
    flows = list(report.get("ph_flows") or [])
    for flow in flows:
        if flow.get("funnel_version") == variant:
            return flow
    if funnel_id:
        for flow in flows:
            if flow.get("funnel_id") == funnel_id:
                return flow
    return None


def _learning(result: str, variant: str) -> str:
    if result == "insufficient_data":
        return f"{variant} is missing or below min landing people; wait for the next growth-loop run."
    if result == "directionally_better":
        return f"{variant} landing→CTA looks higher than the stored proxy baseline; not causal."
    if result == "directionally_worse":
        return f"{variant} landing→CTA looks lower than the stored proxy baseline; not causal."
    return f"{variant} landing→CTA is roughly flat versus the stored proxy baseline; not causal."


def evaluate_run(settings: Settings, run_id: str) -> str:
    row = get_run(settings, run_id)
    proposal = SavedProposal.model_validate(row.proposal)
    variant = row.variant
    if not variant:
        raise ValueError(f"{run_id} has no applied variant")
    weekly, daily = load_latest_reports(settings)
    report = weekly or daily
    if report is None:
        evaluation = Evaluation(
            variant=variant,
            landing_people=0,
            cta_people=0,
            cta_rate=None,
            baseline_proxy_rate=proposal.baseline.cta_rate,
            causal=False,
            result="insufficient_data",
        )
        learning = _learning(evaluation.result, variant)
        update_run(settings, run_id, status="evaluated", evaluation=evaluation, learning=learning)
        return _format(evaluation, learning)

    expected_id = f"recraft-quiz-{variant.replace('_', '-')}"
    flow = _flow_for_variant(report, variant, expected_id)
    if flow is None:
        evaluation = Evaluation(
            variant=variant,
            landing_people=0,
            cta_people=0,
            cta_rate=None,
            baseline_proxy_rate=proposal.baseline.cta_rate,
            causal=False,
            result="insufficient_data",
        )
        learning = _learning(evaluation.result, variant)
        update_run(settings, run_id, status="evaluated", evaluation=evaluation, learning=learning)
        return (
            _format(evaluation, learning)
            + "\n\nVariant missing from the latest report. Wait for the next growth-loop run."
        )

    snapshot = {"ph_flows": [flow], "period": report.get("period"), "run_date": report.get("run_date")}
    measured = baseline_from_report(snapshot, base_version=settings.base_version)
    assert measured is not None
    proxy = proposal.baseline.cta_rate
    if measured.landing_people < settings.min_landing_people:
        result = "insufficient_data"
        rate: float | None = measured.cta_rate
    else:
        rate = measured.cta_rate
        delta = rate - proxy
        if abs(delta) < FLAT_ABS:
            result = "flat"
        elif delta > 0:
            result = "directionally_better"
        else:
            result = "directionally_worse"
    evaluation = Evaluation(
        variant=variant,
        landing_people=measured.landing_people,
        cta_people=measured.cta_people,
        cta_rate=rate,
        baseline_proxy_rate=proxy,
        causal=False,
        result=result,
    )
    learning = _learning(result, variant)
    update_run(settings, run_id, status="evaluated", evaluation=evaluation, learning=learning)
    return _format(evaluation, learning)


def _format(evaluation: Evaluation, learning: str) -> str:
    rate = "n/a" if evaluation.cta_rate is None else f"{evaluation.cta_rate:.1%}"
    proxy = "n/a" if evaluation.baseline_proxy_rate is None else f"{evaluation.baseline_proxy_rate:.1%}"
    return (
        f"Variant: {evaluation.variant}\n"
        f"Landing people: {evaluation.landing_people}\n"
        f"CTA people: {evaluation.cta_people}\n"
        f"CTA rate: {rate}\n"
        f"Baseline proxy rate: {proxy}\n"
        f"causal: {str(evaluation.causal).lower()}\n"
        f"result: {evaluation.result}\n"
        f"Learning: {learning}"
    )
