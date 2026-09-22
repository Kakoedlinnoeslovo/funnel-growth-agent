from __future__ import annotations

from funnel_growth_agent.media import build_tile_spec, generate_tile
from funnel_growth_agent.models import ShowcaseImagePlan
from funnel_growth_agent.tile_sheet import render_sheet, write_sheet
from redesign_helpers import BRIEF_TILE, FakeJudge, fake_media_tools


def test_sheet_shows_prompt_scores_chosen_and_fallback(settings) -> None:
    plan = ShowcaseImagePlan.model_validate(BRIEF_TILE)
    spec = build_tile_spec(plan, settings, "v7")
    result = generate_tile(
        spec, settings, fake_media_tools(judge=FakeJudge(pick=2)), lambda *_: None, variants=3
    )
    path = write_sheet(settings, "run-1", [result])
    html = path.read_text()
    assert path.parent == settings.media_cache_dir / "sheets"
    assert "Showcase tiles for run-1" in html
    assert spec.prompt[:40] in html and spec.stem in html
    assert html.count("class='cand chosen'") == 1 and "#2 <span class='badge'>chosen</span>" in html
    assert "total 9.0" in html and "tile:Vectors:1" in html
    assert f"../tiles/{spec.stem}.cand0.png" in html
    fallback = generate_tile(
        build_tile_spec(plan.model_copy(update={"background": "other"}), settings, "v7"),
        settings,
        fake_media_tools(judge=False),
        lambda *_: None,
        variants=1,
    )
    text = render_sheet("run-2", [fallback], base_dir=path.parent)
    assert "judge fallback" in text


def test_sheet_shows_the_judge_family_for_non_studio_tiles(settings) -> None:
    plan = ShowcaseImagePlan.model_validate(
        {
            **BRIEF_TILE,
            "medium": "ugc-candid",
            "brief": "A woman in her twenties holding up the sticker sheet she just printed",
            "background": None,
        }
    )
    result = generate_tile(
        build_tile_spec(plan, settings, "v7"),
        settings,
        fake_media_tools(),
        lambda *_: None,
        variants=1,
    )
    html = render_sheet("run-3", [result], base_dir=settings.media_cache_dir)
    assert "<span class='pill'>ugc</span>" in html
    studio = generate_tile(
        build_tile_spec(ShowcaseImagePlan.model_validate(BRIEF_TILE), settings, "v7"),
        settings,
        fake_media_tools(),
        lambda *_: None,
        variants=1,
    )
    assert "<span class='pill'>default</span>" not in render_sheet(
        "r", [studio], base_dir=settings.media_cache_dir
    )
