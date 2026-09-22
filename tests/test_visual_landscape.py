from __future__ import annotations

import json

from funnel_growth_agent.agent import ToolLoop
from funnel_growth_agent.models import (
    CachedCreativeAnalysis,
    CachedShowcaseStyle,
    CreativeAnalysis,
    GroupStyle,
    PreviousRun,
    RankedCreative,
    TileStyleRead,
)
from funnel_growth_agent.research import CachedResearch, LandingPatternRead
from funnel_growth_agent.visual_landscape import (
    classify_medium_text,
    families_tested,
    family_of_format,
    load_cached_research,
    visual_landscape,
)
from redesign_helpers import FakeBrowser, FakeReader, FakeStyleReader, redesign_payload


def _ranked(creative_id: str, spend: float = 0, clicks: int = 0) -> RankedCreative:
    return RankedCreative.model_validate(
        {
            "creativeId": creative_id,
            "spend": spend,
            "clicks": clicks,
            "reasonSelected": "test",
        }
    )


def _analysis(
    creative_id: str, fmt: str | None, camera: str | None = None
) -> CachedCreativeAnalysis:
    return CachedCreativeAnalysis(
        creative_id=creative_id,
        asset_fingerprint="x",
        analyzed_at="2026-09-21T00:00:00+00:00",
        model="fake",
        schema_version=3,
        analysis=CreativeAnalysis(
            visual_hook="h",
            primary_promise="p",
            audience_intent="a",
            cta_intent="c",
            suggested_landing_theme="t",
            format=fmt,
            camera_feel=camera,
        ),
    )


def _research(url: str, formats: list[str]) -> CachedResearch:
    return CachedResearch(
        url=url,
        fetched_at="2026-09-21T00:00:00+00:00",
        read=LandingPatternRead.model_validate({"imageryFormats": formats}),
        schema_version=2,
    )


def _style(*mediums: tuple[str | None, str | None]) -> CachedShowcaseStyle:
    tiles = [TileStyleRead(medium=medium, format=fmt) for medium, fmt in mediums]
    return CachedShowcaseStyle(
        base_version="v7",
        landing_hash="h",
        read_at="2026-09-21T00:00:00+00:00",
        groups=[GroupStyle(label="Vectors", tiles=tiles)],
    )


def _run(
    run_id: str, medium: str | None, result: str | None, status: str = "applied"
) -> PreviousRun:
    payload = redesign_payload()
    if medium is None:
        payload["changes"]["media"]["showcase"] = []
    else:
        payload["changes"]["media"]["showcase"][0]["medium"] = medium
    return PreviousRun(
        run_id=run_id,
        experiment_type="landing_redesign",
        hypothesis="h",
        changes=payload["changes"],
        result=result,
        learning="worse" if result == "directionally_worse" else None,
        status=status,
    )


def test_format_and_medium_classification() -> None:
    assert family_of_format("ugc-selfie") == "ugc"
    assert family_of_format("screenshot") == "screen"
    assert family_of_format("before-after", "phone") == "ugc"
    assert family_of_format("before-after", "graphic") == "graphic"
    assert family_of_format("before-after", None) is None
    assert family_of_format("testimonial", None) == "ugc"
    assert family_of_format("other", "studio") == "studio"
    assert family_of_format(None) is None
    assert classify_medium_text("flat vector") == "graphic"
    assert classify_medium_text("candid phone photo") == "ugc"
    assert classify_medium_text("photo") == "studio"
    assert classify_medium_text("") is None


def test_ad_shares_are_spend_weighted_with_clicks_then_count_fallback() -> None:
    ranked = [_ranked("a", spend=300), _ranked("b", spend=100), _ranked("c", spend=100)]
    analyses = [
        _analysis("a", "ugc-candid"),
        _analysis("b", "studio-product"),
        _analysis("c", "before-after", "phone"),
    ]
    landscape = visual_landscape(ranked, analyses, [], None, [])
    shares = {f.family: f.ad_share for f in landscape.families}
    assert shares["ugc"] == 0.8 and shares["studio"] == 0.2 and shares["graphic"] == 0
    ugc = next(f for f in landscape.families if f.family == "ugc")
    assert ugc.ad_creative_ids == ["a", "c"]
    clicks = visual_landscape(
        [_ranked("a", clicks=30), _ranked("b", clicks=10)], analyses[:2], [], None, []
    )
    assert {f.family: f.ad_share for f in clicks.families}["ugc"] == 0.75
    count = visual_landscape([_ranked("a"), _ranked("b")], analyses[:2], [], None, [])
    assert {f.family: f.ad_share for f in count.families}["ugc"] == 0.5
    unknown = visual_landscape(
        [_ranked("a", spend=10), _ranked("z", spend=10)], analyses[:1], [], None, []
    )
    assert {f.family: f.ad_share for f in unknown.families}["ugc"] == 0.5
    assert any("no format read" in note for note in unknown.notes)


def test_gap_lists_blocked_and_untested() -> None:
    ranked = [_ranked("a", spend=60), _ranked("b", spend=40)]
    analyses = [_analysis("a", "ugc-selfie"), _analysis("b", "screenshot")]
    research = [
        _research("https://www.kittl.com/", ["lifestyle", "illustration"]),
        _research("https://www.canva.com/", ["screenshot"]),
    ]
    style = _style(("flat vector", "illustration"), ("photo", None), ("3d clay", None))
    previous = [
        _run("run-3", "photo", "directionally_worse"),
        _run("run-2", "flat-vector", "directionally_better"),
        _run("run-1", None, None),
    ]
    landscape = visual_landscape(ranked, analyses, research, style, previous)
    by_family = {f.family: f for f in landscape.families}
    assert by_family["graphic"].landing_tiles == 2 and by_family["studio"].landing_tiles == 1
    assert by_family["ugc"].landing_tiles == 0
    assert landscape.ads_validate_landing_lacks == ["ugc", "screen"]
    assert landscape.competitors_use_we_lack == ["editorial", "screen"]
    assert by_family["editorial"].competitor_urls == ["https://www.kittl.com/"]
    assert landscape.blocked_without_new_evidence == ["studio"]
    assert by_family["studio"].tested[0].run_id == "run-3"
    assert by_family["studio"].tested[0].learning == "worse"
    assert by_family["graphic"].tested[0].mediums == ["flat-vector"]
    assert landscape.untested == ["ugc", "editorial", "screen"]
    assert any(note.startswith("screenshot ads carry 40%") for note in landscape.notes)
    mediums = {card.medium: card for card in landscape.mediums}
    assert mediums["ugc-candid"].family == "ugc" and mediums["ugc-candid"].people
    assert mediums["lettering"].allows_text and mediums["device-screen"].family == "screen"
    assert json.loads(landscape.model_dump_json(by_alias=True))["adsValidateLandingLacks"]


def test_stale_competitor_reads_are_flagged() -> None:
    old = _research("https://www.canva.com/", [])
    old.schema_version = 1
    landscape = visual_landscape([], [], [old], None, [])
    assert any(
        "predate imagery formats" in note and "canva.com" in note for note in landscape.notes
    )
    assert all(not f.competitor_urls for f in landscape.families)


def test_families_tested_ignores_raw_prompt_tiles() -> None:
    run = _run("run-1", "photo", None)
    run.changes["media"]["showcase"].append({"group": "Vectors", "slot": 1, "prompt": "x" * 30})
    tested = families_tested([run])
    assert [t.run_id for t in tested["studio"]] == ["run-1"] and tested["ugc"] == []


def test_tool_loop_exposes_get_visual_landscape(settings) -> None:
    loop = ToolLoop(
        settings,
        ranked=[_ranked("ad_1", spend=100)],
        analyses=[_analysis("ad_1", "ugc-candid")],
        browser=FakeBrowser(),
        reader=FakeReader(),
        style_reader=FakeStyleReader(),
    )
    loop.execute("research_landing", {"url": "https://www.kittl.com/"})
    result = loop.execute("get_visual_landscape", {})
    families = {f["family"]: f for f in result["families"]}
    assert families["ugc"]["adShare"] == 1.0
    assert families["ugc"]["competitorUrls"] == ["https://www.kittl.com/"]
    assert families["graphic"]["landingTiles"] == 3 and families["studio"]["landingTiles"] == 3
    assert result["adsValidateLandingLacks"] == ["ugc"]
    assert "ugc" in result["untested"] and result["blockedWithoutNewEvidence"] == []
    assert any(card["medium"] == "ugc-selfie" for card in result["mediums"])
    # Research written by an earlier session is picked up from the cache directory too.
    fresh = ToolLoop(settings, ranked=[], analyses=[], style_reader=FakeStyleReader())
    assert [r.url for r in load_cached_research(settings)] == ["https://www.kittl.com/"]
    assert fresh.execute("get_visual_landscape", {})["competitorsUseWeLack"] == ["ugc", "screen"]
    settings.glam_api_key = "glm_x"
    assert any("glam" in note for note in fresh.execute("get_visual_landscape", {})["notes"])
