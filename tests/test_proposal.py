from __future__ import annotations

import json

from funnel_growth_agent.cli import format_propose
from funnel_growth_agent.models import LandingProposal, NoExperiment
from funnel_growth_agent.proposal import propose
from funnel_growth_agent.quality_gate import score_proposal, valid_proposal


class ScriptedModel:
    def __init__(self, output, tools: list[str] | None = None) -> None:
        self.output = output
        self.tools = tools or ["get_landing_cta_metrics"]
        self.seen: list[str] = []

    def complete(self, _messages, _system, execute) -> object:
        for name in self.tools:
            execute(name, {})
            self.seen.append(name)
        return self.output


def _experiment() -> LandingProposal:
    return LandingProposal.model_validate(
        {
            "decision": "experiment",
            "experimentType": "hero_copy",
            "problem": "Hero CTA is generic versus paid JPG→SVG ads.",
            "evidence": ["Proxy landing→CTA baseline is 11.9% from recraft-quiz v6."],
            "hypothesis": "Matching the hero CTA to convert-an-image intent will lift landing_cta_rate.",
            "primaryMetric": "landing_cta_rate",
            "changes": {"ctaLabel": "Convert my image"},
            "otherIdeas": ["Rewrite only the subhead.", "Test a shorter headline."],
        }
    )


def test_propose_writes_memory_only_and_leaves_pricing_lab_and_instructions(settings) -> None:
    landing_before = settings.landing_path.read_bytes()
    site_before = settings.site_path.read_bytes()
    instructions = settings.instructions_path.read_bytes()
    result = propose(settings, model=ScriptedModel(_experiment()))
    assert result.decision == "experiment"
    assert result.baseline.is_proxy is True
    assert settings.memory_path.is_file()
    assert settings.landing_path.read_bytes() == landing_before
    assert settings.site_path.read_bytes() == site_before
    assert settings.instructions_path.read_bytes() == instructions
    text = format_propose(result, include_apply=False)
    assert "No files were modified" in text.splitlines()[-1]
    assert "Apply with" not in text


def test_propose_can_return_no_experiment(settings) -> None:
    result = propose(
        settings,
        model=ScriptedModel(NoExperiment(reason="Landing already matches the paid ads.")),
    )
    assert result.decision == "no_experiment"
    assert "No files were modified" in format_propose(result, include_apply=False)


def test_invalid_model_output_writes_no_memory(settings) -> None:
    class BadModel:
        def complete(self, _messages, _system, _execute):
            return {"decision": "experiment", "experimentType": "composition"}

    try:
        propose(settings, model=BadModel())
    except Exception:
        pass
    else:
        raise AssertionError("invalid output must fail")
    assert not settings.memory_path.is_file()


def test_weak_creatives_do_not_block_propose(settings) -> None:
    week = settings.reports_dir / "2026-09-18_week" / "report_data.json"
    data = json.loads(week.read_text())
    data["creatives"] = []
    week.write_text(json.dumps(data))
    result = propose(settings, model=ScriptedModel(_experiment()))
    assert result.creative_evidence == []
    assert result.decision == "experiment"


def test_quality_gate_marks_grounded_hero_copy_valid(settings) -> None:
    result = propose(settings, model=ScriptedModel(_experiment()))
    context = {
        "metrics": result.baseline.model_dump(by_alias=True),
        "previous": [],
        "creatives": [item.model_dump(by_alias=True) for item in result.creative_evidence],
    }
    card = score_proposal(result, context)
    assert card["grounded"] is True
    assert valid_proposal(result, card) is True
