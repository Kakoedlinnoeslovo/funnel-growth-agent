from __future__ import annotations

import json

import pytest

from funnel_growth_agent.apply import apply_run
from funnel_growth_agent.memory import get_run
from funnel_growth_agent.models import LandingProposal
from funnel_growth_agent.proposal import propose


class ScriptedModel:
    def __init__(self, output) -> None:
        self.output = output

    def complete(self, _messages, _system, execute) -> object:
        execute("get_landing_cta_metrics", {})
        return self.output


def _proposal(**changes: object) -> LandingProposal:
    payload = {
        "decision": "experiment",
        "experimentType": "hero_copy",
        "problem": "Hero CTA is generic versus paid JPG→SVG ads.",
        "evidence": ["Proxy landing→CTA baseline is 11.9% from recraft-quiz v6."],
        "hypothesis": "A convert-image CTA will lift landing_cta_rate.",
        "primaryMetric": "landing_cta_rate",
        "changes": {"ctaLabel": "Convert my image"},
        "otherIdeas": ["Rewrite only the subhead."],
    }
    if changes:
        payload["changes"] = {**payload["changes"], **changes}
    return LandingProposal.model_validate(payload)


def test_apply_publishes_v7_a1_without_touching_v7_or_default(settings) -> None:
    saved = propose(settings, model=ScriptedModel(_proposal()))
    v7_before = settings.landing_path.read_bytes()
    calls = {"n": 0}

    def validate(_directory) -> None:
        calls["n"] += 1

    text = apply_run(settings, saved.run_id, validate=validate)
    assert calls["n"] == 2
    variant = settings.pricing_lab_dir / "funnels" / "v7_a1"
    assert variant.is_dir()
    landing = (variant / "steps" / "landing.yaml").read_text()
    assert "Convert my image" in landing
    funnel = (variant / "funnel.yaml").read_text()
    assert "recraft-quiz-v7-a1" in funnel
    assert "pricing-lab-v7-a1" in funnel
    site = settings.site_path.read_text()
    assert "default_version: v7" in site
    assert "v7_a1" in site
    assert settings.landing_path.read_bytes() == v7_before
    assert get_run(settings, saved.run_id).status == "applied"
    assert get_run(settings, saved.run_id).variant == "v7_a1"
    assert "http://localhost:5173/pm/v7_a1" in text
    assert "✓ proposal valid" in text


def test_stale_hash_is_rejected(settings) -> None:
    saved = propose(settings, model=ScriptedModel(_proposal()))
    settings.landing_path.write_text(settings.landing_path.read_text() + "# drift\n")
    with pytest.raises(ValueError, match="hash"):
        apply_run(settings, saved.run_id, validate=lambda _d: None)
    assert not (settings.pricing_lab_dir / "funnels" / "v7_a1").exists()


def test_existing_variant_not_overwritten(settings) -> None:
    saved = propose(settings, model=ScriptedModel(_proposal()))
    target = settings.pricing_lab_dir / "funnels" / "v7_a1"
    target.mkdir()
    (target / "marker").write_text("keep")
    apply_run(settings, saved.run_id, validate=lambda _d: None)
    assert (target / "marker").read_text() == "keep"
    assert (settings.pricing_lab_dir / "funnels" / "v7_a2" / "funnel.yaml").is_file()
    assert get_run(settings, saved.run_id).variant == "v7_a2"


def test_hard_diff_rejects_funnel_fields_outside_allowlist(settings) -> None:
    saved = propose(settings, model=ScriptedModel(_proposal()))

    def sneaky_validate(directory) -> None:
        funnel = directory / "v7_a1" / "funnel.yaml"
        if funnel.is_file():
            funnel.write_text(funnel.read_text() + "extra: nope\n")

    with pytest.raises(ValueError, match="allowlist|hard"):
        apply_run(settings, saved.run_id, validate=sneaky_validate)


def test_second_validate_failure_rolls_back(settings) -> None:
    saved = propose(settings, model=ScriptedModel(_proposal()))
    site_before = settings.site_path.read_bytes()

    def validate(_directory) -> None:
        if getattr(validate, "seen", False):
            raise RuntimeError("second funnel:validate failed")
        validate.seen = True

    with pytest.raises(RuntimeError, match="second"):
        apply_run(settings, saved.run_id, validate=validate)
    assert not (settings.pricing_lab_dir / "funnels" / "v7_a1").exists()
    assert settings.site_path.read_bytes() == site_before
    assert get_run(settings, saved.run_id).status == "proposed"


def test_forbidden_media_change_is_rejected(settings) -> None:
    raw = json.loads(_proposal().model_dump_json(by_alias=True))
    raw["changes"]["video"] = {"src": "clip.mp4"}
    with pytest.raises(Exception):
        LandingProposal.model_validate(raw)


def test_variant_description_is_one_clean_sentence() -> None:
    from funnel_growth_agent.apply import DESCRIPTION_MAX, describe_variant

    long = "The hero CTA is generic. " + "Second sentence that should be dropped. " * 5
    line = describe_variant("v8_a1", long)
    assert line == "Agent hero-copy experiment v8_a1: The hero CTA is generic."
    huge = describe_variant("v8_a1", "word " * 80)
    assert len(huge) <= DESCRIPTION_MAX
    assert huge.endswith("…")
    assert describe_variant("v8_a1", None) == "Agent hero-copy experiment v8_a1"


def test_apply_refuses_a_run_that_is_already_applied(settings) -> None:
    saved = propose(settings, model=ScriptedModel(_proposal()))
    apply_run(settings, saved.run_id, validate=lambda _d: None)
    with pytest.raises(ValueError, match="applied as v7_a1"):
        apply_run(settings, saved.run_id, validate=lambda _d: None)
    assert not (settings.pricing_lab_dir / "funnels" / "v7_a2").exists()
    assert get_run(settings, saved.run_id).variant == "v7_a1"


def test_apply_keeps_site_comments_and_list_style(settings) -> None:
    settings.site_path.write_text(
        "# top comment\ndefault_version: v7\npublished_versions:\n  - v7\n"
        "# trailing comment\nversions:\n  v7: Onboarding quiz\n",
        encoding="utf-8",
    )
    saved = propose(settings, model=ScriptedModel(_proposal()))
    apply_run(settings, saved.run_id, validate=lambda _d: None)
    site = settings.site_path.read_text()
    assert "# top comment" in site
    assert "# trailing comment" in site
    assert "  - v7\n" in site and "  - v7_a1\n" in site
    # The new entry goes right after v7; the comment that followed the list still follows it.
    assert site.index("  - v7_a1\n") < site.index("# trailing comment")
    landing = (
        settings.pricing_lab_dir / "funnels" / "v7_a1" / "steps" / "landing.yaml"
    ).read_text()
    assert "    - component: kittl-hero\n" in landing
