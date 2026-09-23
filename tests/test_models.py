from __future__ import annotations

import pytest
from pydantic import ValidationError

from funnel_growth_agent.models import (
    HeroCopyChanges,
    LandingProposal,
    NoExperiment,
    RedesignChanges,
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


def test_landing_redesign_parses_all_groups() -> None:
    from redesign_helpers import redesign_payload

    proposal = parse_model_output(redesign_payload())
    assert isinstance(proposal, LandingProposal)
    assert isinstance(proposal.changes, RedesignChanges)
    changes = proposal.changes
    assert changes.copy_changes.hero.cta_label == "Vectorize my image"
    assert changes.copy_changes.inline_cta.cta_label == "Vectorize my image"
    assert changes.layout.layout == "video-first"
    assert changes.composition.omit == ["style-switcher", "inline-cta-2"]
    assert changes.media.hero_video.stem == "hero-youtube-z0r74lakhom-30-120"
    assert changes.media.showcase[0].tile_model == "nano_banana_2"
    assert changes.media.showcase[0].references == ["tile:Vectors:1"]
    dumped = proposal.model_dump(by_alias=True, exclude_none=True)
    assert "copy" in dumped["changes"] and "heroVideo" in dumped["changes"]["media"]
    assert LandingProposal.model_validate(dumped).changes == changes


def test_redesign_headline_shapes_are_coerced() -> None:
    changes = RedesignChanges.model_validate(
        {"copy": {"hero": {"headline": "One line"}, "finalCta": {"headline": ["Two", "lines"]}}}
    )
    assert changes.copy_changes.hero.headline == ["One line"]
    assert changes.copy_changes.final_cta.headline == "Two lines"


def test_redesign_rejects_empty_changes() -> None:
    with pytest.raises(ValidationError, match="empty"):
        RedesignChanges.model_validate({})
    with pytest.raises(ValidationError, match="empty"):
        RedesignChanges.model_validate({"copy": {"hero": {}}, "media": {}})
    with pytest.raises(ValidationError):
        RedesignChanges.model_validate({"copy": {"hero": {"ctaLabel": ""}}})


def test_redesign_rejects_unknown_keys_and_locked_content() -> None:
    with pytest.raises(ValidationError):
        RedesignChanges.model_validate({"copy": {"hero": {"video": {"src": "x.mp4"}}}})
    with pytest.raises(ValidationError):
        RedesignChanges.model_validate({"copy": {"inlineCta": {"subhead": "no"}}})
    with pytest.raises(ValidationError):
        RedesignChanges.model_validate(
            {
                "media": {
                    "showcase": [
                        {"group": "Vectors", "slot": 0, "prompt": "x" * 30, "caption": "no"}
                    ]
                }
            }
        )
    with pytest.raises(ValidationError):
        RedesignChanges.model_validate({"prices": {"monthly": 1}})


def test_composition_rules() -> None:
    with pytest.raises(ValidationError, match="at most 2"):
        RedesignChanges.model_validate({"composition": {"omit": ["a", "b", "c"]}})
    with pytest.raises(ValidationError, match="kittl-hero"):
        RedesignChanges.model_validate({"composition": {"order": ["kittl-hero", "logo-strip"]}})
    with pytest.raises(ValidationError, match="both ordered and omitted"):
        RedesignChanges.model_validate(
            {"composition": {"order": ["showcase"], "omit": ["showcase"]}}
        )
    with pytest.raises(ValidationError, match="repeat"):
        RedesignChanges.model_validate({"composition": {"order": ["showcase", "showcase"]}})


def test_hero_video_plan_requires_matching_id_and_bounds() -> None:
    base = {"label": "Recraft Vectorize in action"}
    with pytest.raises(ValidationError, match="videoId"):
        RedesignChanges.model_validate({"media": {"heroVideo": {"source": "youtube", **base}}})
    with pytest.raises(ValidationError, match="creativeId"):
        RedesignChanges.model_validate({"media": {"heroVideo": {"source": "creative", **base}}})
    with pytest.raises(ValidationError):
        RedesignChanges.model_validate(
            {"media": {"heroVideo": {"source": "youtube", "videoId": "too-short", **base}}}
        )
    with pytest.raises(ValidationError):
        RedesignChanges.model_validate(
            {
                "media": {
                    "heroVideo": {
                        "source": "youtube",
                        "videoId": "z0r74lakHOM",
                        "duration": 45,
                        **base,
                    }
                }
            }
        )
    with pytest.raises(ValidationError, match="repeat"):
        RedesignChanges.model_validate(
            {
                "media": {
                    "showcase": [
                        {"group": "Vectors", "slot": 0, "prompt": "x" * 30},
                        {"group": "Vectors", "slot": 0, "prompt": "y" * 30},
                    ]
                }
            }
        )


def test_legacy_showcase_rows_still_parse() -> None:
    from funnel_growth_agent.models import ShowcaseImagePlan
    from redesign_helpers import LEGACY_TILE

    plan = ShowcaseImagePlan.model_validate(LEGACY_TILE)
    assert plan.tile_model == "nano_banana_2"
    assert plan.prompt == LEGACY_TILE["prompt"] and plan.brief is None
    assert plan.aspect_ratio == "16:9"
    assert plan.model_dump(by_alias=True)["aspectRatio"] == "16:9"
    with pytest.raises(ValidationError, match="unknown route"):
        ShowcaseImagePlan.model_validate({**LEGACY_TILE, "route": "dalle"})


def test_showcase_brief_rules() -> None:
    from funnel_growth_agent.models import ShowcaseImagePlan
    from redesign_helpers import BRIEF_TILE

    plan = ShowcaseImagePlan.model_validate(BRIEF_TILE)
    assert plan.tile_model == "nano_banana_2" and plan.lettering_text is None
    with pytest.raises(ValidationError, match="exactly one of brief or prompt"):
        ShowcaseImagePlan.model_validate({**BRIEF_TILE, "prompt": "x" * 30})
    with pytest.raises(ValidationError, match="exactly one of brief or prompt"):
        ShowcaseImagePlan.model_validate({"group": "Vectors", "slot": 0})
    with pytest.raises(ValidationError, match="needs a medium"):
        ShowcaseImagePlan.model_validate({**BRIEF_TILE, "medium": None})
    with pytest.raises(ValidationError, match="palette of 2 or 3"):
        ShowcaseImagePlan.model_validate({**BRIEF_TILE, "palette": ["#111111"]})
    with pytest.raises(ValidationError, match="not a hex colour"):
        ShowcaseImagePlan.model_validate({**BRIEF_TILE, "palette": ["#12", "red"]})
    with pytest.raises(ValidationError, match="must be creative"):
        ShowcaseImagePlan.model_validate({**BRIEF_TILE, "references": ["photo:1"]})
    with pytest.raises(ValidationError, match="references repeat"):
        ShowcaseImagePlan.model_validate(
            {**BRIEF_TILE, "references": ["tile:Vectors:1", "tile:Vectors:1"]}
        )
    assert ShowcaseImagePlan.model_validate({**BRIEF_TILE, "model": "nano_banana_2"}).references
    assert ShowcaseImagePlan.model_validate({**BRIEF_TILE, "medium": "ugc-candid"}).medium
    assert ShowcaseImagePlan.model_validate({**BRIEF_TILE, "medium": "device-screen"}).medium
    with pytest.raises(ValidationError):
        ShowcaseImagePlan.model_validate({**BRIEF_TILE, "medium": "ugc-screenshot"})
    with pytest.raises(ValidationError, match="double quotes"):
        ShowcaseImagePlan.model_validate(
            {**BRIEF_TILE, "medium": "lettering", "brief": "The word VECTOR in retro lettering"}
        )
    lettering = ShowcaseImagePlan.model_validate(
        {**BRIEF_TILE, "medium": "lettering", "brief": 'The word "VECTOR" in retro lettering'}
    )
    assert lettering.lettering_text == "VECTOR"


def test_previous_reference_needs_an_earlier_slot() -> None:
    from funnel_growth_agent.models import MediaPlan
    from redesign_helpers import BRIEF_TILE

    with pytest.raises(ValidationError, match="'previous' needs an earlier slot"):
        MediaPlan.model_validate({"showcase": [{**BRIEF_TILE, "references": ["previous"]}]})
    plan = MediaPlan.model_validate(
        {
            "showcase": [
                BRIEF_TILE,
                {**BRIEF_TILE, "slot": 2, "references": ["previous"]},
            ]
        }
    )
    assert plan.showcase[1].references == ["previous"]


def test_creative_analysis_v1_payload_still_parses_and_v2_fields_coerce() -> None:
    from funnel_growth_agent.models import CreativeAnalysis

    v1 = CreativeAnalysis.model_validate(
        {
            "visualHook": "x",
            "primaryPromise": "y",
            "audienceIntent": "z",
            "ctaIntent": "c",
            "suggestedLandingTheme": "t",
        }
    )
    assert v1.palette == [] and v1.visual_elements == [] and v1.medium is None
    v2 = CreativeAnalysis.model_validate(
        {
            "visualHook": "x",
            "primaryPromise": "y",
            "audienceIntent": "z",
            "ctaIntent": "c",
            "suggestedLandingTheme": "t",
            "palette": "#C8F520\n#111111",
            "visualElements": ["before/after split of one object"],
            "composition": "headline top, object centre",
            "medium": "flat vector",
        }
    )
    assert v2.palette == ["#C8F520", "#111111"]


def test_saved_proposal_old_hero_copy_rows_still_parse() -> None:
    from funnel_growth_agent.models import SavedProposal

    saved = SavedProposal.model_validate(
        {
            "runId": "2026-09-18T1918-v7-001",
            "baseVersion": "v7",
            "baseLandingHash": "abc",
            "baseline": {
                "funnelId": "recraft-quiz",
                "version": "v6",
                "isProxy": True,
                "landingPeople": 1000,
                "ctaPeople": 100,
                "ctaRate": 0.1,
            },
            "decision": "experiment",
            "experimentType": "hero_copy",
            "changes": {"ctaLabel": "Vectorize My Image", "videoCta": None, "finalCta": None},
        }
    )
    assert isinstance(saved.changes, HeroCopyChanges)
    assert saved.changes.cta_label == "Vectorize My Image"


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
