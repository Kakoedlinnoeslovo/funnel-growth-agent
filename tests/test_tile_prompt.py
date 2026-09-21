from __future__ import annotations

import pytest

from funnel_growth_agent.models import ShowcaseImagePlan
from funnel_growth_agent.tile_prompt import (
    COMPOSITION,
    ReferenceRole,
    build_tile_prompt,
    prompt_word_count,
    variant_prompts,
)
from redesign_helpers import BRIEF_TILE, LEGACY_TILE


def _plan(**overrides: object) -> ShowcaseImagePlan:
    payload = dict(BRIEF_TILE)
    payload.update(overrides)
    return ShowcaseImagePlan.model_validate(payload)


def test_flat_vector_prompt_has_every_house_style_part() -> None:
    prompt = build_tile_prompt(
        _plan(), "Logos and icons.", [ReferenceRole("tile", "tile:Vectors:1")]
    )
    assert prompt.startswith("A flat vector illustration of One strawberry split")
    assert "bold 3px outlines" in prompt and "editable SVG paths" in prompt
    assert 'It belongs to the "Vectors" gallery of a landing page: Logos and icons.' in prompt
    assert "Use only #C8F520, #111111 and #FFFFFF with one dominant colour." in prompt
    assert "The background is a flat solid black canvas." in prompt
    assert COMPOSITION in prompt
    assert "The first image is a style reference from the same gallery" in prompt
    assert prompt.endswith("No text.")
    assert 60 <= prompt_word_count(prompt) <= 200
    assert build_tile_prompt(_plan(), "Logos and icons.", [ReferenceRole("tile", "x")]) == prompt


@pytest.mark.parametrize(
    ("medium", "needle"),
    [
        ("photo", "85mm lens"),
        ("illustration", "brush and pencil texture"),
        ("3d-icons", "matte clay"),
        ("mockup", "one real physical object"),
    ],
)
def test_each_medium_uses_its_technique_nouns(medium: str, needle: str) -> None:
    prompt = build_tile_prompt(_plan(medium=medium, background=None), None)
    assert needle in prompt
    assert "The background is a" in prompt  # per-medium default
    assert "gallery of a landing page." in prompt


def test_lettering_quotes_the_words_and_forbids_other_text() -> None:
    plan = _plan(
        medium="lettering",
        brief='The word "VECTOR" as chunky retro display lettering with a single swash',
        palette=["#FF3B30", "#FFF8E7"],
        background=None,
    )
    prompt = build_tile_prompt(plan, None)
    assert 'the words "VECTOR" set as custom display lettering' in prompt
    assert "as chunky retro display lettering with a single swash" in prompt
    assert prompt.count('"VECTOR"') == 2
    assert prompt.endswith('Render exactly the text "VECTOR" and no other text.')
    assert "single-colour field" in prompt


def test_reference_roles_follow_ordinals() -> None:
    roles = [
        ReferenceRole("tile", "tile:Vectors:0"),
        ReferenceRole("creative", "creative:1"),
        ReferenceRole("previous", "previous"),
    ]
    prompt = build_tile_prompt(_plan(), None, roles)
    assert "The first image is a style reference" in prompt
    assert "The second image is a winning ad" in prompt
    assert "The third image is the neighbouring tile" in prompt
    assert prompt.index("first image") < prompt.index("second image") < prompt.index("third image")


def test_raw_prompt_passes_through_untouched() -> None:
    plan = ShowcaseImagePlan.model_validate(LEGACY_TILE)
    assert build_tile_prompt(plan, "caption", [ReferenceRole("tile", "x")]) == LEGACY_TILE["prompt"]


def test_variant_prompts_differ_on_one_axis_before_the_text_rule() -> None:
    base = build_tile_prompt(_plan(), None)
    variants = variant_prompts(base, 3)
    assert variants[0] == base and len(variants) == 3 and len(set(variants)) == 3
    for variant in variants[1:]:
        assert variant.endswith("No text.") and variant.startswith(base[:-8])
    assert "three-quarter view" in variants[1]
    assert variant_prompts(base, 1) == [base]
    assert len(variant_prompts(base, 9)) == 4
    assert variant_prompts(base, 3) == variants
