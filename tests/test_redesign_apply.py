from __future__ import annotations

import pytest
from ruamel.yaml import YAML

from funnel_growth_agent.apply import ApplyError, apply_run, check_media_plan, describe_variant
from funnel_growth_agent.landing import resolve_section_ids
from funnel_growth_agent.memory import get_run
from funnel_growth_agent.models import SavedProposal
from funnel_growth_agent.proposal import propose
from redesign_helpers import (
    FakeImages,
    FakeRunner,
    ScriptedModel,
    fake_media_tools,
    redesign_payload,
    redesign_proposal,
)


def _lab_digest(settings) -> dict[str, bytes]:
    root = settings.pricing_lab_dir / "funnels"
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
        and not str(path.relative_to(root)).startswith("v7_a")
        and path.name != "site.yaml"
    }


def test_redesign_apply_publishes_media_copy_and_order(settings) -> None:
    saved = propose(settings, model=ScriptedModel(redesign_proposal()))
    before = _lab_digest(settings)
    runner = FakeRunner()
    images = FakeImages()
    events: list[tuple[str, str]] = []
    text = apply_run(
        settings,
        saved.run_id,
        validate=lambda _d: None,
        media_tools=fake_media_tools(runner=runner, images=images),
        on_event=lambda stage, message: events.append((stage, message)),
    )
    variant = settings.pricing_lab_dir / "funnels" / "v7_a1"
    assert variant.is_dir()
    videos = sorted(path.name for path in (variant / "assets" / "video").iterdir())
    assert len(videos) == 6 and all(
        name.startswith("hero-youtube-z0r74lakhom-30-120") for name in videos
    )
    tiles = sorted(path.name for path in (variant / "assets" / "showcase").iterdir())
    assert len(tiles) == 7 and any(name.startswith("gen-") for name in tiles)
    thumbs = sorted(path.name for path in (variant / "assets" / "thumbs" / "showcase").iterdir())
    assert len(thumbs) == 1 and thumbs[0].startswith("gen-")
    doc = YAML(typ="safe").load((variant / "steps" / "landing.yaml").read_text())
    sections = doc["props"]["sections"]
    assert resolve_section_ids(sections) == [
        "kittl-hero",
        "press-quotes",
        "logo-strip",
        "showcase",
        "inline-cta",
        "video-cta",
        "plan-preview",
        "final-cta",
    ]
    hero = sections[0]
    assert hero["layout"] == "video-first"
    assert hero["video"]["desktop"]["mp4"].startswith("assets/video/hero-youtube")
    assert hero["ctaLabel"] == "Vectorize my image"
    assert sections[-1]["ctaLabel"] == "Vectorize my image"
    funnel = (variant / "funnel.yaml").read_text()
    assert "Landing redesign experiment v7_a1" in funnel
    site = settings.site_path.read_text()
    assert "Agent landing-redesign experiment v7_a1" in site and "default_version: v7" in site
    assert _lab_digest(settings) == before
    assert get_run(settings, saved.run_id).status == "applied"
    stages = [stage for stage, _ in events]
    assert stages[:2] == ["copy", "media"]
    assert ("media", "encoded 1280 mp4") in events
    assert any(
        message.startswith("sections: kittl-hero, press-quotes")
        for stage, message in events
        if stage == "patch"
    )
    assert "✓ 8 media files produced (0 cached)" in text
    assert "✓ tile gen-" in text and "Vectors slot 0 #1 (" in text
    assert "✓ sections: kittl-hero, press-quotes" in text
    assert "http://localhost:5173/pm/v7_a1" in text
    assert len(images.calls) == 1 and len(images.calls[0]["prompts"]) == 3


def test_image_failure_leaves_no_variant_and_keeps_the_clip_cache(settings) -> None:
    saved = propose(settings, model=ScriptedModel(redesign_proposal()))
    runner = FakeRunner()
    site_before = settings.site_path.read_bytes()
    with pytest.raises(ApplyError, match="image generation failed"):
        apply_run(
            settings,
            saved.run_id,
            validate=lambda _d: None,
            media_tools=fake_media_tools(runner=runner, images=FakeImages(fail=True)),
        )
    assert not (settings.pricing_lab_dir / "funnels" / "v7_a1").exists()
    assert settings.site_path.read_bytes() == site_before
    assert get_run(settings, saved.run_id).status == "proposed"
    assert (settings.media_cache_dir / "youtube" / "z0r74lakHOM.mp4").is_file()
    assert not list(settings.tmp_dir.iterdir())


def test_second_validate_failure_rolls_back_and_retry_reuses_cache(settings) -> None:
    saved = propose(settings, model=ScriptedModel(redesign_proposal()))
    runner = FakeRunner()
    tools = fake_media_tools(runner=runner)
    calls = {"n": 0}

    def flaky(_directory) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("second funnel:validate failed")

    with pytest.raises(RuntimeError, match="second"):
        apply_run(settings, saved.run_id, validate=flaky, media_tools=tools)
    assert not (settings.pricing_lab_dir / "funnels" / "v7_a1").exists()
    assert get_run(settings, saved.run_id).status == "proposed"
    first_round = len(runner.calls)
    text = apply_run(settings, saved.run_id, validate=lambda _d: None, media_tools=tools)
    assert len(runner.calls) == first_round
    assert "(8 cached)" in text
    assert (settings.pricing_lab_dir / "funnels" / "v7_a1" / "assets" / "video").is_dir()


def test_tiles_preview_then_apply_makes_no_new_image_requests(settings) -> None:
    from funnel_growth_agent.media import generate_tiles

    saved = propose(settings, model=ScriptedModel(redesign_proposal()))
    images = FakeImages()
    tools = fake_media_tools(images=images)
    proposal = SavedProposal.model_validate(get_run(settings, saved.run_id).proposal)
    results = generate_tiles(proposal.changes.media, settings, tools, lambda *_: None, variants=2)
    assert len(results) == 1 and len(images.calls) == 1
    text = apply_run(settings, saved.run_id, validate=lambda _d: None, media_tools=tools)
    assert len(images.calls) == 1
    assert "(2 cached)" in text


def test_creative_reference_outside_evidence_is_refused_before_any_call(settings) -> None:
    payload = redesign_payload()
    payload["changes"]["media"]["showcase"][0]["references"] = ["creative:ad_unknown"]
    saved = propose(settings, model=ScriptedModel(payload))
    images = FakeImages()
    runner = FakeRunner()
    with pytest.raises(ApplyError, match="not among this proposal's ranked creatives"):
        apply_run(
            settings,
            saved.run_id,
            validate=lambda _d: None,
            media_tools=fake_media_tools(runner=runner, images=images),
        )
    assert runner.calls == [] and images.calls == []
    payload["changes"]["media"]["showcase"][0]["references"] = ["tile:Vectors:3"]
    saved = propose(settings, model=ScriptedModel(payload))
    with pytest.raises(ApplyError, match="does not exist on the base landing"):
        apply_run(settings, saved.run_id, validate=lambda _d: None, media_tools=fake_media_tools())


def test_youtube_id_outside_catalog_is_refused_before_any_download(settings) -> None:
    payload = redesign_payload()
    payload["changes"]["media"]["heroVideo"]["videoId"] = "AAAAAAAAAAA"
    saved = propose(settings, model=ScriptedModel(payload))
    runner = FakeRunner()
    with pytest.raises(ApplyError, match="not in the catalog"):
        apply_run(
            settings,
            saved.run_id,
            validate=lambda _d: None,
            media_tools=fake_media_tools(runner=runner),
        )
    assert runner.calls == []
    assert not settings.tmp_dir.exists() or not list(settings.tmp_dir.iterdir())


def test_creative_clip_must_come_from_the_proposals_ranked_creatives(settings) -> None:
    saved = propose(settings, model=ScriptedModel(redesign_proposal()))
    proposal = SavedProposal.model_validate(get_run(settings, saved.run_id).proposal)
    plan = proposal.changes.media.model_copy(deep=True)
    plan.hero_video = plan.hero_video.model_copy(
        update={"source": "creative", "video_id": None, "creative_id": "ad_unknown"}
    )
    with pytest.raises(ApplyError, match="not among this proposal"):
        check_media_plan(plan, proposal, settings)
    plan.hero_video = plan.hero_video.model_copy(update={"creative_id": "ad_1"})
    with pytest.raises(ApplyError, match="no local mp4"):
        check_media_plan(plan, proposal, settings)


def test_redesign_without_media_needs_no_tools(settings) -> None:
    saved = propose(settings, model=ScriptedModel(redesign_proposal(media=None, layout=None)))
    text = apply_run(settings, saved.run_id, validate=lambda _d: None)
    assert "media files produced" not in text
    variant = settings.pricing_lab_dir / "funnels" / "v7_a1"
    assert not (variant / "assets" / "video").exists()
    doc = YAML(typ="safe").load((variant / "steps" / "landing.yaml").read_text())
    assert doc["props"]["sections"][0]["layout"] == "copy-first"
    assert doc["props"]["sections"][1]["component"] == "press-quotes"


def test_describe_variant_for_redesign() -> None:
    assert describe_variant(
        "v8_a2", "First screen sells generic creation.", "landing_redesign"
    ) == ("Agent landing-redesign experiment v8_a2: First screen sells generic creation.")
