from __future__ import annotations

from pathlib import Path

from funnel_growth_agent.apply import apply_run
from funnel_growth_agent.config import load_settings
from funnel_growth_agent.models import LandingProposal
from funnel_growth_agent.proposal import propose

# Real pricing-lab checkout and its current default version, resolved from .env / site.yaml.
# Skipped when the checkout is absent (CI, fresh clone).
try:
    _real = load_settings()
    REAL_LAB: Path | None = _real.pricing_lab_dir
    REAL_VERSION = _real.base_version
except (FileNotFoundError, ValueError):
    REAL_LAB = None
    REAL_VERSION = ""
REAL_STEPS = (REAL_LAB or Path()) / "funnels" / REAL_VERSION / "steps"
REAL_LANDING = REAL_STEPS / "landing.yaml"
REAL_FUNNEL = REAL_STEPS.parent / "funnel.yaml"
REAL_PLAN = REAL_STEPS / "plan.yaml"


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


def test_apply_patches_real_landing_without_touching_paywall(settings) -> None:
    if REAL_LAB is None or not REAL_LANDING.is_file():
        return
    settings.landing_path.write_bytes(REAL_LANDING.read_bytes())
    settings.funnel_path.write_bytes(REAL_FUNNEL.read_bytes())
    plan = settings.pricing_lab_dir / "funnels" / "v7" / "steps" / "plan.yaml"
    plan.write_bytes(REAL_PLAN.read_bytes())
    before = settings.landing_path.read_bytes()
    paywall_before = plan.read_text()
    saved = propose(settings, model=ScriptedModel())
    apply_run(settings, saved.run_id, validate=lambda _d: None)
    variant_landing = (
        settings.pricing_lab_dir / "funnels" / "v7_a1" / "steps" / "landing.yaml"
    ).read_text()
    variant_plan = (
        settings.pricing_lab_dir / "funnels" / "v7_a1" / "steps" / "plan.yaml"
    ).read_text()
    assert "Convert my image" in variant_landing
    assert "Convert a file" in variant_landing
    assert f"paywallId: {REAL_VERSION}-quiz" in variant_plan
    assert variant_plan == paywall_before
    assert settings.landing_path.read_bytes() == before
    assert "video:" in variant_landing
    assert "_shared/assets/video/vector-editor-1920.webm" in variant_landing


def test_redesign_applies_to_real_landing_with_fake_media(settings) -> None:
    if REAL_LAB is None or not REAL_LANDING.is_file():
        return
    from ruamel.yaml import YAML

    from funnel_growth_agent.landing import resolve_section_ids
    from redesign_helpers import ScriptedModel as ArgScriptedModel
    from redesign_helpers import fake_media_tools, redesign_payload

    settings.landing_path.write_bytes(REAL_LANDING.read_bytes())
    settings.funnel_path.write_bytes(REAL_FUNNEL.read_bytes())
    plan = settings.pricing_lab_dir / "funnels" / "v7" / "steps" / "plan.yaml"
    plan.write_bytes(REAL_PLAN.read_bytes())
    real_doc = YAML(typ="safe").load(REAL_LANDING.read_text())
    real_ids = resolve_section_ids(real_doc["props"]["sections"])
    showcase = next(s for s in real_doc["props"]["sections"] if s["component"] == "showcase")
    group_label = showcase["groups"][0]["label"]
    payload = redesign_payload()
    payload["changes"]["composition"] = {"omit": ["style-switcher"]}
    payload["changes"]["media"]["showcase"][0]["group"] = group_label
    payload["changes"]["media"]["showcase"][0]["references"] = [f"tile:{group_label}:0"]
    if "inline-cta" not in real_ids:
        payload["changes"]["copy"].pop("inlineCta")
    before = settings.landing_path.read_bytes()
    saved = propose(settings, model=ArgScriptedModel(payload))
    apply_run(settings, saved.run_id, validate=lambda _d: None, media_tools=fake_media_tools())
    variant = settings.pricing_lab_dir / "funnels" / "v7_a1"
    variant_landing = (variant / "steps" / "landing.yaml").read_text()
    doc = YAML(typ="safe").load(variant_landing)
    ids = resolve_section_ids(doc["props"]["sections"])
    assert ids == [sid for sid in real_ids if sid != "style-switcher"]
    hero = doc["props"]["sections"][0]
    assert hero["video"]["desktop"]["webm"].startswith("assets/video/hero-youtube")
    assert "_shared/assets/video/vector-editor-1920.webm" not in variant_landing
    assert (variant / "steps" / "plan.yaml").read_bytes() == REAL_PLAN.read_bytes()
    assert settings.landing_path.read_bytes() == before
    assert len(list((variant / "assets" / "video").iterdir())) == 6
