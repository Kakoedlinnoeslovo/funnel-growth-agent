from __future__ import annotations

import json

from funnel_growth_agent.evaluate import evaluate_run
from funnel_growth_agent.memory import get_run
from funnel_growth_agent.models import LandingProposal
from funnel_growth_agent.proposal import propose


class ScriptedModel:
    def complete(self, _messages, _system, execute) -> object:
        execute("get_landing_cta_metrics", {})
        return LandingProposal.model_validate(
            {
                "decision": "experiment",
                "experimentType": "hero_copy",
                "problem": "Hero CTA is generic versus paid JPG→SVG ads.",
                "evidence": ["Proxy landing→CTA baseline is 11.9% from recraft-quiz v6."],
                "hypothesis": "A convert-image CTA will lift landing_cta_rate.",
                "primaryMetric": "landing_cta_rate",
                "changes": {"ctaLabel": "Convert my image"},
                "otherIdeas": ["Rewrite only the subhead."],
            }
        )


def _apply_row(settings, variant: str = "v7_a1") -> str:
    from funnel_growth_agent.apply import apply_run

    saved = propose(settings, model=ScriptedModel())
    apply_run(settings, saved.run_id, validate=lambda _d: None)
    row = get_run(settings, saved.run_id)
    assert row.variant == variant
    return saved.run_id


def test_evaluate_writes_insufficient_data_when_variant_absent(settings) -> None:
    run_id = _apply_row(settings)
    text = evaluate_run(settings, run_id)
    row = get_run(settings, run_id)
    assert row.evaluation is not None
    assert row.evaluation.result == "insufficient_data"
    assert row.evaluation.causal is False
    assert row.status == "evaluated"
    assert "insufficient_data" in text
    assert "causal: false" in text.lower() or "causal=false" in text.replace(" ", "").lower()


def test_evaluate_is_directional_and_never_causal(settings) -> None:
    run_id = _apply_row(settings)
    week = settings.reports_dir / "2026-09-18_week" / "report_data.json"
    data = json.loads(week.read_text())
    data["ph_flows"].insert(
        0,
        {
            "funnel_id": "recraft-quiz-v7-a1",
            "funnel_version": "v7_a1",
            "landing_people": 800,
            "payments": 0,
            "rows": [
                {"node_id": "landing", "kind": "screen", "viewed": 800, "continue_rate": 0.1375},
                {"node_id": "making", "kind": "screen", "viewed": 110, "continue_rate": 0.5},
            ],
        },
    )
    week.write_text(json.dumps(data))
    text = evaluate_run(settings, run_id)
    row = get_run(settings, run_id)
    assert row.evaluation is not None
    assert row.evaluation.causal is False
    assert row.evaluation.result == "directionally_better"
    assert row.evaluation.cta_rate == 0.1375
    assert row.learning
    assert "directionally_better" in text
    from funnel_growth_agent.memory import get_previous_runs

    history = get_previous_runs(settings, experiment_type="hero_copy", limit=5)
    assert history[0].learning == row.learning
    assert history[0].evaluation is not None
    assert history[0].evaluation.causal is False


def test_evaluate_below_min_people_is_insufficient(settings) -> None:
    run_id = _apply_row(settings)
    week = settings.reports_dir / "2026-09-18_week" / "report_data.json"
    data = json.loads(week.read_text())
    data["ph_flows"].insert(
        0,
        {
            "funnel_id": "recraft-quiz-v7-a1",
            "funnel_version": "v7_a1",
            "landing_people": 20,
            "payments": 0,
            "rows": [
                {"node_id": "landing", "kind": "screen", "viewed": 20, "continue_rate": 0.2},
                {"node_id": "making", "kind": "screen", "viewed": 4, "continue_rate": 0.5},
            ],
        },
    )
    week.write_text(json.dumps(data))
    evaluate_run(settings, run_id)
    assert get_run(settings, run_id).evaluation.result == "insufficient_data"
    assert get_run(settings, run_id).evaluation.causal is False
