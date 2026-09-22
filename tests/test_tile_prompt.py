from __future__ import annotations

import pytest

from funnel_growth_agent.models import ShowcaseImagePlan
from funnel_growth_agent.tile_prompt import (
    COMPOSITION,
    FAMILY_MEDIUMS,
    MEDIUM_STYLES,
    UGC_COMPOSITION,
    ReferenceRole,
    build_tile_prompt,
    family_of_medium,
    prompt_word_count,
    style_of,
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
        ("ugc-selfie", "front-camera phone selfie"),
        ("ugc-candid", "across the table"),
        ("ugc-unboxing", "overhead phone photo"),
        ("lifestyle", "35mm lens"),
        ("device-screen", "no readable interface text"),
    ],
)
def test_each_medium_uses_its_technique_nouns(medium: str, needle: str) -> None:
    prompt = build_tile_prompt(_plan(medium=medium, background=None), None)
    assert needle in prompt
    assert "The background is a" in prompt  # per-medium default
    assert "gallery of a landing page." in prompt
    assert prompt.endswith("No text.")
    assert 60 <= prompt_word_count(prompt) <= 200


def test_ugc_prompt_uses_ugc_composition_palette_and_identity_lock() -> None:
    plan = _plan(
        medium="ugc-candid",
        brief="A woman in her twenties holding up the sticker sheet she just printed from her drawing",
        background=None,
    )
    roles = [ReferenceRole("tile", "tile:Vectors:0"), ReferenceRole("previous", "previous")]
    prompt = build_tile_prompt(plan, "Logos and icons.", roles)
    assert "shot on an iPhone" in prompt and "no phone UI or device frame" in prompt
    assert UGC_COMPOSITION in prompt and COMPOSITION not in prompt
    assert "Let #C8F520, #111111 and #FFFFFF lead the frame" in prompt
    assert "Use only" not in prompt
    assert "The background is a kitchen or cafe table" in prompt
    assert "this one is a phone photo of a real person" in prompt
    assert "shows the same person: reproduce their exact face" in prompt
    assert prompt.endswith("No text.")
    # Studio mediums keep the original reference wording.
    studio = build_tile_prompt(_plan(), None, roles)
    assert "style reference from the same gallery" in studio
    assert "neighbouring tile in this gallery: stay in the same visual family" in studio


def test_variant_axes_follow_medium() -> None:
    base = build_tile_prompt(_plan(medium="ugc-selfie", background=None), None)
    variants = variant_prompts(base, 3, style_of("ugc-selfie").axes)
    assert len(variants) == 3 and "window" in variants[1] and "overcast" in variants[2]
    assert all(v.endswith("No text.") for v in variants)
    assert "three-quarter view" in variant_prompts(base, 2)[1]
    assert len(variant_prompts(base, 9, style_of("ugc-unboxing").axes)) == 4


def test_medium_table_families_and_legacy_names() -> None:
    assert FAMILY_MEDIUMS["graphic"] == ("flat-vector", "lettering", "illustration", "3d-icons")
    assert FAMILY_MEDIUMS["studio"] == ("photo", "mockup")
    assert FAMILY_MEDIUMS["ugc"] == ("ugc-selfie", "ugc-candid", "ugc-unboxing")
    assert FAMILY_MEDIUMS["editorial"] == ("lifestyle",)
    assert FAMILY_MEDIUMS["screen"] == ("device-screen",)
    assert family_of_medium("ugc-candid") == "ugc" and family_of_medium(None) is None
    assert style_of(None) is MEDIUM_STYLES["photo"]
    assert MEDIUM_STYLES["lettering"].allows_text and not MEDIUM_STYLES["photo"].allows_text
    assert all(len(style.axes) == 3 for style in MEDIUM_STYLES.values())
    assert all(style.one_line for style in MEDIUM_STYLES.values())


LEGACY_PROMPTS = {
    "flat-vector": (
        "A flat vector illustration of One strawberry split down the middle, left half a soft "
        "pixelated photo, right half the same strawberry as crisp flat vector shapes. Clean "
        "closed shapes, bold 3px outlines, flat two-tone cel-shading, crisp edges, no gradients "
        "and no texture, artwork that exports as editable SVG paths. It belongs to the "
        '"Vectors" gallery of a landing page. Use only #C8F520, #111111 and #FFFFFF with one '
        "dominant colour. The background is a flat, solid off-white canvas. "
    ),
    "photo": (
        "A photorealistic studio photograph of One strawberry split down the middle, left half "
        "a soft pixelated photo, right half the same strawberry as crisp flat vector shapes. "
        "Shot on an 85mm lens at f/2.8, soft diffused key light from the upper left, true skin "
        "and material texture, shallow depth of field, editorial finish. It belongs to the "
        '"Vectors" gallery of a landing page. Use only #C8F520, #111111 and #FFFFFF with one '
        "dominant colour. The background is a seamless studio sweep. "
    ),
    "illustration": "The background is a flat, lightly textured paper tone. ",
    "3d-icons": "The background is a smooth studio backdrop with a soft floor shadow. ",
    "mockup": "The background is a plain studio surface. ",
}


@pytest.mark.parametrize("medium", sorted(LEGACY_PROMPTS))
def test_legacy_prompts_are_pinned_so_cached_tiles_survive(medium: str) -> None:
    """The cache stem hashes the prompt: rewording a legacy medium re-renders every tile."""
    prompt = build_tile_prompt(_plan(medium=medium, background=None), None)
    assert LEGACY_PROMPTS[medium] in prompt
    assert prompt.endswith(COMPOSITION + " No text.")
    assert variant_prompts(prompt, 2)[1].count("three-quarter view") == 1


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
