from __future__ import annotations

import json
from pathlib import Path

import pytest

from funnel_growth_agent.landing_diff import ApplyError
from funnel_growth_agent.media import (
    build_tile_spec,
    encode_hero_video,
    encoded_file_names,
    fetch_youtube,
    generate_tile,
    generate_tiles,
    produce_media,
    resolve_references,
    score_candidates,
    tile_seeds,
    tile_stem,
)
from funnel_growth_agent.models import HeroVideoPlan, MediaPlan, ShowcaseImagePlan
from redesign_helpers import (
    BRIEF_TILE,
    LEGACY_TILE,
    FakeImages,
    FakeJudge,
    FakeRunner,
    fake_media_tools,
)


def _clip(**overrides: object) -> HeroVideoPlan:
    payload = {
        "source": "youtube",
        "videoId": "z0r74lakHOM",
        "start": 3,
        "duration": 12,
        "label": "Recraft Vectorize turning a JPG into editable paths",
    }
    payload.update(overrides)
    return HeroVideoPlan.model_validate(payload)


def _tile(**overrides: object) -> ShowcaseImagePlan:
    payload = dict(BRIEF_TILE)
    payload.update(overrides)
    return ShowcaseImagePlan.model_validate(payload)


def _events() -> tuple[list[str], object]:
    seen: list[str] = []

    def emit(stage: str, message: str) -> None:
        seen.append(f"{stage}: {message}")

    return seen, emit


def test_image_backend_prefers_glam_then_gemini(settings) -> None:
    from funnel_growth_agent.media import (
        GeminiImageClient,
        GeminiTileJudge,
        HttpGlamClient,
        default_image_client,
        default_media_tools,
    )

    assert default_image_client(settings) is None
    assert default_media_tools(settings).judge is None
    settings.gemini_api_key = "gem"
    assert isinstance(default_image_client(settings), GeminiImageClient)
    assert isinstance(default_media_tools(settings).judge, GeminiTileJudge)
    settings.glam_api_key = "glm_x"
    assert isinstance(default_image_client(settings), HttpGlamClient)


def test_clip_stems_are_deterministic_content_keys() -> None:
    assert _clip().stem == "hero-youtube-z0r74lakhom-30-120"
    assert _clip(start=3.5).stem == "hero-youtube-z0r74lakhom-35-120"
    assert (
        _clip(source="creative", videoId=None, creativeId="ad_1").stem
        == "hero-creative-ad-1-30-120"
    )


def test_tile_stem_changes_with_prompt_model_and_reference_bytes(settings) -> None:
    spec = build_tile_spec(_tile(), settings, "v7")
    assert spec.stem.startswith("gen-") and len(spec.stem) == 16
    assert spec.model_name == "gemini-3.1-flash-image"
    assert "strawberry" in spec.prompt and "Logos and icons" in spec.prompt
    assert build_tile_spec(_tile(), settings, "v7").stem == spec.stem
    pro = build_tile_spec(_tile(model="nano_banana_pro"), settings, "v7")
    assert pro.model_name == "gemini-3-pro-image" and pro.stem != spec.stem
    assert build_tile_spec(_tile(background="a red field"), settings, "v7").stem != spec.stem
    assert tile_stem("other-model", spec.prompt, spec.references) != spec.stem
    ref = settings.pricing_lab_dir / "funnels" / "v7" / "assets" / "showcase" / "vector-2.webp"
    ref.write_bytes(b"changed bytes")
    assert build_tile_spec(_tile(), settings, "v7").stem != spec.stem
    seeds = tile_seeds(spec.stem, 3)
    assert len(set(seeds)) == 3 and seeds == tile_seeds(spec.stem, 3)


def test_resolve_references_tile_creative_previous_and_unknown(settings, tmp_path: Path) -> None:
    refs = resolve_references(_tile(), settings, "v7", None)
    assert [r.kind for r in refs] == ["tile"]
    assert refs[0].path.name == "vector-2.webp"
    with pytest.raises(ApplyError, match="no showcase group 'Nope'"):
        resolve_references(_tile(references=["tile:Nope:0"]), settings, "v7", None)
    with pytest.raises(ApplyError, match="has no image 3"):
        resolve_references(_tile(references=["tile:Vectors:3"]), settings, "v7", None)
    with pytest.raises(ApplyError, match="no local jpg"):
        resolve_references(_tile(references=["creative:ad_1"]), settings, "v7", None)
    live = settings.growth_loop_dir / "data" / "output" / "creative" / "live"
    live.mkdir(parents=True)
    (live / "ad_1.jpg").write_bytes(b"ad")
    refs = resolve_references(
        _tile(references=["creative:ad_1", "tile:Vectors:0"]), settings, "v7", None
    )
    assert [r.kind for r in refs] == ["creative", "tile"]
    with pytest.raises(ApplyError, match="'previous' has no chosen tile"):
        resolve_references(_tile(slot=1, references=["previous"]), settings, "v7", None)
    previous = tmp_path / "prev.webp"
    previous.write_bytes(b"prev")
    refs = resolve_references(_tile(slot=1, references=["previous"]), settings, "v7", previous)
    assert refs[0].kind == "previous" and refs[0].path == previous


def test_score_candidates_recomputes_totals_and_vetoes_stray_text() -> None:
    raw = [
        {
            "index": 1,
            "houseStyle": 9,
            "subjectClarity": 9,
            "cleanliness": 2,
            "cropSafety": 9,
            "paletteAdherence": 9,
        },
        {
            "index": 2,
            "houseStyle": 7,
            "subjectClarity": 7,
            "cleanliness": 8,
            "cropSafety": 7,
            "paletteAdherence": 7,
        },
        {
            "index": "3",
            "houseStyle": "7",
            "subjectClarity": 7,
            "cleanliness": 8,
            "cropSafety": 7,
            "paletteAdherence": 7,
        },
    ]
    scores, chosen = score_candidates(raw, 3)
    assert [s.index for s in scores] == [0, 1, 2]
    assert scores[0].total > scores[1].total
    assert chosen == 1  # candidate 0 vetoed on cleanliness; tie between 1 and 2 -> lowest index
    scores, chosen = score_candidates([], 2)
    assert chosen == 0 and all(s.total == 0 for s in scores)


def test_score_candidates_weights_follow_the_family() -> None:
    from funnel_growth_agent.media import JUDGE_WEIGHTS, JUDGE_WEIGHTS_BY_FAMILY, judge_family

    for family, weights in JUDGE_WEIGHTS_BY_FAMILY.items():
        assert abs(sum(weights.values()) - 1.0) < 1e-9, family
        assert set(weights) == set(JUDGE_WEIGHTS)
    row = {
        "index": 1,
        "houseStyle": 8,
        "subjectClarity": 8,
        "cleanliness": 4,
        "cropSafety": 8,
        "paletteAdherence": 2,
    }
    default_total = score_candidates([row], 1)[0][0].total
    ugc_total = score_candidates([row], 1, "ugc")[0][0].total
    assert ugc_total > default_total  # palette barely counts for a phone photo
    assert score_candidates([{**row, "cleanliness": 3}, {**row, "index": 2}], 2, "ugc")[1] == 1
    assert judge_family("ugc-selfie") == "ugc" and judge_family("lifestyle") == "editorial"
    assert judge_family("device-screen") == "screen"
    assert judge_family("flat-vector") == "default" and judge_family(None) == "default"


def test_judge_prompt_default_rubric_is_unchanged_and_ugc_rubric_differs() -> None:
    from funnel_growth_agent.media import JUDGE_PROMPT, JUDGE_RUBRIC

    def render(family: str) -> str:
        rubric = JUDGE_RUBRIC[family].format(palette="#C8F520, #111111")
        return JUDGE_PROMPT.format(
            ref_count=1, first_cand=2, last_cand=4, prompt="p", rubric=rubric, lettering="none"
        )

    default = render("default")
    assert "- houseStyle: matches the rendering technique, saturation, lighting" in default
    assert "- cleanliness: no stray letters, text, watermarks, logos, UI or rendering" in default
    assert "- paletteAdherence: uses only #C8F520, #111111 with one dominant colour" in default
    assert "Intended text: none. Count intended text as correct, not stray." in default
    ugc = render("ugc")
    assert "extra or fused fingers" in ugc and "real-world clutter is fine" in ugc
    assert "#C8F520, #111111 leads through clothing" in ugc
    assert "readable invented interface text" in render("screen")
    assert set(JUDGE_RUBRIC) == {"default", "ugc", "editorial", "screen"}


def test_generate_tile_passes_the_family_to_the_judge(settings) -> None:
    judge = FakeJudge()
    tools = fake_media_tools(judge=judge)
    plan = _tile(
        medium="ugc-candid",
        brief="A woman in her twenties holding up the sticker sheet she just printed",
        background=None,
    )
    result = generate_tile(
        build_tile_spec(plan, settings, "v7"), settings, tools, lambda *_: None, variants=2
    )
    assert judge.calls[0]["family"] == "ugc"
    assert result.judge is not None and result.judge.family == "ugc"
    generate_tile(
        build_tile_spec(_tile(), settings, "v7"), settings, tools, lambda *_: None, variants=2
    )
    assert judge.calls[1]["family"] == "default"
    without = generate_tile(
        build_tile_spec(plan, settings, "v7"),
        settings,
        fake_media_tools(judge=False),
        lambda *_: None,
        variants=1,
        refresh=True,
    )
    assert without.judge is not None and without.judge.family == "ugc"


def test_generate_tile_makes_candidates_judges_and_caches(settings) -> None:
    runner = FakeRunner()
    images = FakeImages()
    judge = FakeJudge(pick=1)
    tools = fake_media_tools(runner=runner, images=images, judge=judge)
    seen, emit = _events()
    spec = build_tile_spec(_tile(), settings, "v7")
    result = generate_tile(spec, settings, tools, emit, variants=3)
    assert len(result.candidates) == 3 and not result.cached
    call = images.calls[0]
    assert call["model"] == "gemini-3.1-flash-image"
    assert len(set(call["seeds"])) == 3
    assert call["prompts"][0] == spec.prompt and call["prompts"][1] != spec.prompt
    assert call["references"] == ["tile:Vectors:1"]
    assert judge.calls[0]["palette"] == ["#C8F520", "#111111", "#FFFFFF"]
    assert result.judge is not None and result.judge.chosen == 1 and not result.judge.fallback
    assert b"cand1" in result.chosen.read_bytes() and b"cand1" in result.thumb.read_bytes()
    cwebp = runner.calls_for("cwebp")
    assert [argv[argv.index("-resize") + 1] for argv in cwebp] == ["1600", "800"]
    cache = settings.media_cache_dir / "tiles"
    assert (cache / f"{spec.stem}.judge.json").is_file()
    assert (cache / f"{spec.stem}.prompt.txt").read_text() == spec.prompt
    refs = json.loads((cache / f"{spec.stem}.refs.json").read_text())
    assert refs[0]["ref"] == "tile:Vectors:1"
    assert any("3 candidates via gemini-3.1-flash-image [1 refs]" in line for line in seen)
    assert any("judge picked #1" in line for line in seen)
    again = generate_tile(spec, settings, tools, emit, variants=3)
    assert again.cached and len(images.calls) == 1 and len(judge.calls) == 1
    assert again.judge is not None and again.judge.chosen == 1


def test_generate_tile_uses_the_medium_axes_for_variants(settings) -> None:
    images = FakeImages()
    tools = fake_media_tools(images=images)
    plan = _tile(
        medium="ugc-selfie",
        brief="A man in his thirties holding the poster he printed from his sketch",
        background=None,
    )
    spec = build_tile_spec(plan, settings, "v7")
    generate_tile(spec, settings, tools, lambda *_: None, variants=3)
    prompts = images.calls[0]["prompts"]
    assert "window" in prompts[1] and "overcast" in prompts[2]
    assert "three-quarter view" not in prompts[1]
    legacy = FakeImages()
    generate_tile(
        build_tile_spec(_tile(), settings, "v7"),
        settings,
        fake_media_tools(images=legacy),
        lambda *_: None,
        variants=2,
    )
    assert "three-quarter view" in legacy.calls[0]["prompts"][1]


def test_generate_tile_judge_failure_falls_back_to_first_candidate(settings) -> None:
    tools = fake_media_tools(judge=FakeJudge(fail=True))
    seen, emit = _events()
    result = generate_tile(
        build_tile_spec(_tile(), settings, "v7"), settings, tools, emit, variants=2
    )
    assert result.judge is not None and result.judge.fallback and result.judge.chosen == 0
    assert b"cand0" in result.chosen.read_bytes()
    assert any("judge failed" in line for line in seen)
    without = fake_media_tools(judge=False)
    result = generate_tile(
        build_tile_spec(_tile(background="other"), settings, "v7"),
        settings,
        without,
        emit,
        variants=1,
    )
    assert result.judge is not None and result.judge.fallback


def test_generate_tiles_feeds_previous_with_slot_zero_output(settings) -> None:
    images = FakeImages()
    tools = fake_media_tools(images=images)
    plan = MediaPlan(
        showcase=[
            _tile(),
            _tile(slot=1, references=["tile:Vectors:0", "previous"]),
        ]
    )
    results = generate_tiles(plan, settings, tools, lambda *_: None, variants=2)
    assert len(results) == 2 and len(images.calls) == 2
    second = images.calls[1]
    assert second["references"] == ["tile:Vectors:0", "previous"]
    assert second["reference_paths"][1] == results[0].chosen
    assert "neighbouring tile" in second["prompts"][0]


def test_legacy_prompt_tile_passes_raw_prompt_through(settings) -> None:
    plan = ShowcaseImagePlan.model_validate(LEGACY_TILE)
    assert plan.tile_model == "nano_banana_2" and plan.prompt and plan.brief is None
    groups_dir = settings.pricing_lab_dir / "funnels" / "v7" / "steps" / "landing.yaml"
    assert groups_dir.is_file()
    plan = ShowcaseImagePlan.model_validate({**LEGACY_TILE, "group": "Vectors"})
    spec = build_tile_spec(plan, settings, "v7")
    assert spec.prompt == LEGACY_TILE["prompt"]
    assert spec.model_name == "gemini-3.1-flash-image" and spec.references == []


def test_fetch_youtube_uses_ytdlp_then_cache(settings) -> None:
    runner = FakeRunner()
    tools = fake_media_tools(runner=runner)
    seen, emit = _events()
    path = fetch_youtube("z0r74lakHOM", settings, tools, emit)
    assert path == settings.media_cache_dir / "youtube" / "z0r74lakHOM.mp4"
    assert path.is_file()
    argv = runner.calls_for("yt-dlp")[0]
    assert argv[-1] == "https://www.youtube.com/watch?v=z0r74lakHOM"
    assert "--merge-output-format" in argv
    fetch_youtube("z0r74lakHOM", settings, tools, emit)
    assert len(runner.calls_for("yt-dlp")) == 1
    assert seen[-1] == "media: youtube z0r74lakHOM: cached"


def test_encode_hero_video_matches_lab_recipe_and_is_idempotent(settings, tmp_path: Path) -> None:
    source = tmp_path / "src.mp4"
    source.write_bytes(b"src")
    runner = FakeRunner()
    tools = fake_media_tools(runner=runner)
    seen, emit = _events()
    out = encode_hero_video(source, _clip(), settings, tools, emit)
    names = encoded_file_names(_clip().stem)
    assert all((out / name).is_file() for name in names)
    ffmpeg = runner.calls_for("ffmpeg")
    assert len(ffmpeg) == 6  # mp4, webm, poster frame at two widths
    mp4 = ffmpeg[0]
    assert mp4[mp4.index("-ss") + 1] == "3" and mp4[mp4.index("-t") + 1] == "12"
    assert "-an" in mp4 and "libx264" in mp4 and "+faststart" in mp4
    assert "scale=1280:-2:flags=lanczos,fps=30,format=yuv420p" in mp4
    assert "libvpx-vp9" in ffmpeg[1] and "-row-mt" in ffmpeg[1]
    assert "-frames:v" in ffmpeg[2]
    assert len(runner.calls_for("cwebp")) == 2
    assert not list(out.glob("*.png"))
    assert "media: encoded 1280 mp4" in seen and "media: poster 720" in seen
    encode_hero_video(source, _clip(), settings, tools, emit)
    assert len(runner.calls_for("ffmpeg")) == 6


def test_produce_media_stages_clip_tiles_and_thumbs(settings, tmp_path: Path) -> None:
    variant = tmp_path / "v7_a1"
    variant.mkdir()
    plan = MediaPlan(
        heroVideo=_clip(),
        showcase=[_tile(), _tile(group="Photos", slot=2, references=["tile:Photos:0"])],
    )
    seen, emit = _events()
    produced = produce_media(plan, variant, settings, fake_media_tools(), emit, variants=2)
    assert len(produced.files.hero_video) == 6
    assert len(produced.files.showcase) == 2 and len(produced.files.showcase_thumbs) == 2
    assert all((variant / rel).is_file() for rel in produced.files.all)
    assert all(rel.startswith("assets/thumbs/showcase/") for rel in produced.files.showcase_thumbs)
    ref = produced.hero_video_ref
    assert ref["desktop"]["mp4"] == f"assets/video/{_clip().stem}-1280.mp4"
    assert ref["phone"]["poster"] == f"assets/video/{_clip().stem}-poster-720.webp"
    assert "aspect" not in ref["desktop"]
    assert produced.showcase[("Photos", 2)] != produced.showcase[("Vectors", 0)]
    assert len(produced.tiles) == 2 and produced.cached_files == 0


@pytest.mark.parametrize(
    ("start", "duration", "source_duration"),
    [
        (2, 28, "30.0"),
        (0, 30, "30.0"),
        (2, 27.95, "30.0"),
        (2, 28, "29.974"),
        (2, 28, "29.9"),
    ],
)
def test_produce_media_accepts_clip_at_source_end(
    settings, tmp_path: Path, start: float, duration: float, source_duration: str
) -> None:
    runner = FakeRunner(duration=source_duration)
    plan = MediaPlan(heroVideo=_clip(start=start, duration=duration))
    variant = tmp_path / "v7_a1"
    produced = produce_media(
        plan, variant, settings, fake_media_tools(runner=runner), lambda *_: None
    )
    assert len(produced.files.hero_video) == 6
    assert all((variant / rel).is_file() for rel in produced.files.hero_video)
    ffmpeg = runner.calls_for("ffmpeg")
    assert len(ffmpeg) == 6
    assert ffmpeg[0][ffmpeg[0].index("-ss") + 1] == f"{start:g}"
    assert ffmpeg[0][ffmpeg[0].index("-t") + 1] == f"{duration:g}"


@pytest.mark.parametrize("source_duration", ["10.0", "29.89", "29.899999"])
def test_produce_media_rejects_clip_past_source_end(
    settings, tmp_path: Path, source_duration: str
) -> None:
    variant = tmp_path / "v7_a1"
    variant.mkdir()
    runner = FakeRunner(duration=source_duration)
    plan = MediaPlan(heroVideo=_clip(start=2, duration=28))
    with pytest.raises(ApplyError, match="exceeds source length"):
        produce_media(plan, variant, settings, fake_media_tools(runner=runner), lambda *_: None)
    assert not runner.calls_for("ffmpeg")


def test_produce_media_without_image_backend_fails_before_any_download(
    settings, tmp_path: Path
) -> None:
    variant = tmp_path / "v7_a1"
    variant.mkdir()
    runner = FakeRunner()
    plan = MediaPlan(heroVideo=_clip(), showcase=[_tile()])
    with pytest.raises(ApplyError, match="no image backend"):
        produce_media(
            plan, variant, settings, fake_media_tools(runner=runner, images=False), lambda *_: None
        )
    assert runner.calls == []


def test_creative_source_must_exist_locally(settings, tmp_path: Path) -> None:
    variant = tmp_path / "v7_a1"
    variant.mkdir()
    plan = MediaPlan(heroVideo=_clip(source="creative", videoId=None, creativeId="ad_1"))
    with pytest.raises(ApplyError, match="no local mp4"):
        produce_media(plan, variant, settings, fake_media_tools(), lambda *_: None)
    live = settings.growth_loop_dir / "data" / "output" / "creative" / "live"
    live.mkdir(parents=True)
    (live / "ad_1.mp4").write_bytes(b"ad")
    produced = produce_media(plan, variant, settings, fake_media_tools(), lambda *_: None)
    assert any("hero-creative-ad-1" in rel for rel in produced.files.hero_video)


def test_failed_tool_is_reported_with_its_stderr(settings, tmp_path: Path) -> None:
    variant = tmp_path / "v7_a1"
    variant.mkdir()
    runner = FakeRunner(fail=("libvpx-vp9",))
    with pytest.raises(ApplyError, match="ffmpeg webm 1280 failed: fake failure"):
        produce_media(
            MediaPlan(heroVideo=_clip()),
            variant,
            settings,
            fake_media_tools(runner=runner),
            lambda *_: None,
        )


def test_asset_role_composition_and_message_change_cache_key(settings):
    original = build_tile_spec(_tile(), settings, "v7")
    for extra in [
        dict(role="hero"),
        dict(composition="Keep the subject to the right, with open space for a headline."),
        dict(intendedMessage="Show a consistent icon family"),
        dict(aspectRatio="4:3"),
        dict(artDirection="Warm paper and lime accents"),
    ]:
        assert build_tile_spec(_tile(**extra), settings, "v7").stem != original.stem


def test_gemini_nano_banana_2_accepts_references_and_requested_aspect(
    settings, tmp_path, monkeypatch
):
    from types import SimpleNamespace as NS

    from google import genai

    from funnel_growth_agent.media import GeminiImageClient, HttpGlamClient

    plan = _tile(model="nano_banana_2", aspectRatio="4:3")
    spec = build_tile_spec(plan, settings, "v7")
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs)
        return NS(candidates=[NS(content=NS(parts=[NS(inline_data=NS(data=b"generated image"))]))])

    monkeypatch.setattr(
        genai, "Client", lambda **_: NS(aio=NS(models=NS(generate_content=generate)))
    )
    client = GeminiImageClient("test-key")
    dest = tmp_path / "candidate.png"
    assert client.generate_candidates(
        model=spec.model_name,
        prompts=[spec.prompt],
        references=spec.references,
        seeds=[1],
        dests=[dest],
        aspect_ratio=plan.aspect_ratio,
    ) == [dest]
    assert calls[0]["config"].image_config.aspect_ratio == "4:3"
    assert len(calls[0]["contents"]) == len(spec.references) + 1
    with pytest.raises(RuntimeError, match="no reference images"):
        HttpGlamClient("test").generate_candidates(
            model=spec.model_name,
            prompts=[spec.prompt],
            references=spec.references,
            seeds=[1],
            dests=[dest],
        )
