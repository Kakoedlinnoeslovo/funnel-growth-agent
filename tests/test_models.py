from __future__ import annotations

import pytest
from pydantic import ValidationError

from funnel_growth_agent.models import (
    HeroCopyChanges,
    LandingProposal,
    NoExperiment,
    parse_model_output,
)


def test_hero_copy_experiment_accepts_allowed_text_fields() -> None:
    proposal = parse_model_output(
        {
            "decision": "experiment",
            "experimentType": "hero_copy",
            "problem": "Hero does not match paid JPG to SVG intent.",
            "evidence": ["Top ads promise image-to-vector conversion."],
            "hypothesis": "Matching the hero to that promise will lift landing_cta_rate.",
            "primaryMetric": "landing_cta_rate",
            "changes": {
                "headline": ["Drop a JPG.", "Get an editable SVG."],
                "subhead": "Upload a logo and edit every path.",
                "ctaLabel": "Convert my image",
                "reassurance": "Free to use. No credit card required.",
                "videoCta": {"headline": ["Try the converter"], "ctaLabel": "Convert a file"},
                "finalCta": {"headline": "Ready to convert?", "ctaLabel": "Start Creating"},
            },
            "otherIdeas": ["Tighten the subhead only.", "Change only the hero CTA."],
        }
    )
    assert isinstance(proposal, LandingProposal)
    assert proposal.experiment_type == "hero_copy"
    assert proposal.changes.cta_label == "Convert my image"


def test_nested_hero_object_is_flattened() -> None:
    proposal = parse_model_output(
        {
            "decision": "experiment",
            "experimentType": "hero_copy",
            "problem": "Hero CTA is generic versus paid JPG→SVG ads.",
            "evidence": ["Proxy landing→CTA baseline is 11.9% from recraft-quiz v6."],
            "hypothesis": "A convert-image CTA will lift landing_cta_rate.",
            "primaryMetric": "landing_cta_rate",
            "changes": {
                "hero": {
                    "ctaLabel": "Vectorize My Image",
                    "reassurance": "Free to use. No credit card required. Works on any device.",
                    "headline": ["Drop a JPG.", "Get an editable SVG."],
                },
                "videoCta": {"ctaLabel": "Convert a file"},
                "finalCta": {"ctaLabel": "Vectorize My Image"},
            },
            "otherIdeas": ["Rewrite only the subhead."],
        }
    )
    assert isinstance(proposal, LandingProposal)
    assert proposal.changes.cta_label == "Vectorize My Image"
    assert proposal.changes.reassurance.startswith("Free to use")
    assert proposal.changes.headline == ["Drop a JPG.", "Get an editable SVG."]
    assert proposal.changes.video_cta is not None
    assert proposal.changes.video_cta.cta_label == "Convert a file"


def test_no_experiment_is_valid_without_changes() -> None:
    proposal = parse_model_output(
        {"decision": "no_experiment", "reason": "No strong evidence for a change."}
    )
    assert isinstance(proposal, NoExperiment)
    assert proposal.reason.startswith("No strong")


def test_no_experiment_keeps_other_ideas() -> None:
    proposal = parse_model_output(
        {
            "decision": "no_experiment",
            "reason": "A proposal is already queued.",
            "otherIdeas": ["Apply the queued run first."],
        }
    )
    assert isinstance(proposal, NoExperiment)
    assert proposal.other_ideas == ["Apply the queued run first."]


def test_composition_and_hero_layout_are_rejected_on_claude_output() -> None:
    with pytest.raises(ValidationError):
        parse_model_output(
            {
                "decision": "experiment",
                "experimentType": "composition",
                "problem": "x",
                "evidence": ["y"],
                "hypothesis": "z",
                "primaryMetric": "landing_cta_rate",
                "changes": {"omit": ["press-quotes"]},
                "otherIdeas": [],
            }
        )
    with pytest.raises(ValidationError):
        parse_model_output(
            {
                "decision": "experiment",
                "experimentType": "hero_layout",
                "problem": "x",
                "evidence": ["y"],
                "hypothesis": "z",
                "primaryMetric": "landing_cta_rate",
                "changes": {"layout": "copy-first"},
                "otherIdeas": [],
            }
        )


def test_forbidden_price_and_media_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        HeroCopyChanges.model_validate({"monthly": 20})
    with pytest.raises(ValidationError):
        HeroCopyChanges.model_validate({"src": "assets/x.webp"})
    with pytest.raises(ValidationError):
        parse_model_output(
            {
                "decision": "experiment",
                "experimentType": "hero_copy",
                "problem": "x",
                "evidence": ["y"],
                "hypothesis": "z",
                "primaryMetric": "landing_cta_rate",
                "changes": {"headline": ["Hi"], "video": {"src": "clip.mp4"}},
                "otherIdeas": ["a"],
            }
        )


def test_mixed_or_missing_experiment_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_model_output(
            {
                "decision": "experiment",
                "experimentType": "hero_copy",
                "problem": "x",
                "evidence": ["y"],
                "hypothesis": "z",
                "primaryMetric": "revenue",
                "changes": {"headline": ["Hi"]},
                "otherIdeas": ["a"],
            }
        )
