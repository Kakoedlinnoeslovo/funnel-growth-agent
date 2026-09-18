from __future__ import annotations

from pathlib import Path

from funnel_growth_agent.apply import apply_run
from funnel_growth_agent.models import LandingProposal
from funnel_growth_agent.proposal import propose

REAL_LANDING = Path("/Users/roman/Desktop/all/recraft-pricing-lab/funnels/v7/steps/landing.yaml")
REAL_FUNNEL = Path("/Users/roman/Desktop/all/recraft-pricing-lab/funnels/v7/funnel.yaml")
REAL_PLAN = Path("/Users/roman/Desktop/all/recraft-pricing-lab/funnels/v7/steps/plan.yaml")


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
                "changes": {
                    "ctaLabel": "Convert my image",
                    "videoCta": {"ctaLabel": "Convert a file"},
                    "finalCta": {"ctaLabel": "Convert my image"},
                },
                "otherIdeas": ["Rewrite only the subhead."],
            }
        )


def test_apply_patches_real_v7_landing_without_touching_paywall(settings) -> None:
    if not REAL_LANDING.is_file():
        return
    settings.landing_path.write_bytes(REAL_LANDING.read_bytes())
    settings.funnel_path.write_bytes(REAL_FUNNEL.read_bytes())
    plan = settings.pricing_lab_dir / "funnels" / "v7" / "steps" / "plan.yaml"
    plan.write_bytes(REAL_PLAN.read_bytes())
    before = settings.landing_path.read_bytes()
    paywall_before = plan.read_text()
    saved = propose(settings, model=ScriptedModel())
    apply_run(settings, saved.run_id, validate=lambda _d: None)
    variant_landing = (settings.pricing_lab_dir / "funnels" / "v7_a1" / "steps" / "landing.yaml").read_text()
    variant_plan = (settings.pricing_lab_dir / "funnels" / "v7_a1" / "steps" / "plan.yaml").read_text()
    assert "Convert my image" in variant_landing
    assert "Convert a file" in variant_landing
    assert "paywallId: v7-quiz" in variant_plan
    assert variant_plan == paywall_before
    assert settings.landing_path.read_bytes() == before
    assert "video:" in variant_landing
    assert "_shared/assets/video/vector-editor-1920.webm" in variant_landing
