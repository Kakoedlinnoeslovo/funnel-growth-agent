from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from funnel_growth_agent.gemini import ANALYSIS_SCHEMA_VERSION, analyze_creative, asset_fingerprint
from funnel_growth_agent.models import CreativeAnalysis, RankedCreative


def _ranked(**overrides: object) -> RankedCreative:
    data = {
        "creativeId": "ad_1",
        "adName": "jpg_to_svg_01",
        "title": "Convert JPG to SVG",
        "body": "Turn any image into an editable vector.",
        "reasonSelected": "highest lab payments among comparable ads",
    }
    data.update(overrides)
    return RankedCreative.model_validate(data)


def test_analysis_schema_rejects_cro_decision_fields() -> None:
    with pytest.raises(ValidationError):
        CreativeAnalysis.model_validate(
            {
                "visualHook": "x",
                "primaryPromise": "y",
                "audienceIntent": "z",
                "ctaIntent": "convert",
                "suggestedLandingTheme": "image-to-vector",
                "shouldRunExperiment": True,
            }
        )


def test_cache_hit_skips_second_gemini_call(settings, tmp_path: Path) -> None:
    calls = {"n": 0}

    def fake_call(_creative: RankedCreative, _asset: Path | None) -> CreativeAnalysis:
        calls["n"] += 1
        return CreativeAnalysis(
            visual_hook="JPG becomes SVG",
            primary_promise="Turn an image into a vector",
            audience_intent="Has a file already",
            cta_intent="Convert an image",
            suggested_landing_theme="image-to-vector conversion",
        )

    image = tmp_path / "ad.jpg"
    image.write_bytes(b"fake-bytes")
    creative = _ranked(imagePath=str(image))
    first = analyze_creative(creative, settings, call_model=fake_call)
    second = analyze_creative(creative, settings, call_model=fake_call)
    assert calls["n"] == 1
    assert first.analysis.primary_promise == second.analysis.primary_promise
    assert first.schema_version == ANALYSIS_SCHEMA_VERSION


def test_refresh_and_fingerprint_change_rerun_gemini(settings, tmp_path: Path) -> None:
    calls = {"n": 0}

    def fake_call(_creative: RankedCreative, _asset: Path | None) -> CreativeAnalysis:
        calls["n"] += 1
        return CreativeAnalysis(
            visual_hook=str(calls["n"]),
            primary_promise="p",
            audience_intent="a",
            cta_intent="c",
            suggested_landing_theme="t",
        )

    image = tmp_path / "ad.jpg"
    image.write_bytes(b"one")
    creative = _ranked(imagePath=str(image))
    analyze_creative(creative, settings, call_model=fake_call)
    image.write_bytes(b"two")
    analyze_creative(creative, settings, call_model=fake_call)
    analyze_creative(creative, settings, call_model=fake_call, refresh=True)
    assert calls["n"] == 3


def test_missing_asset_and_key_still_returns_title_body_fallback(settings) -> None:
    creative = _ranked(imagePath="/no/such/file.jpg")
    cached = analyze_creative(creative, settings, call_model=None)
    assert cached.analysis.primary_promise
    assert "jpg" in cached.analysis.primary_promise.lower()
    assert cached.analysis.visible_text == []  # Metadata is not observed visual text.
    assert cached.model == "title-body-fallback"


def test_schema_version_is_four_everywhere_and_v3_caches_rerun(settings, tmp_path: Path) -> None:
    from funnel_growth_agent.config import Settings

    assert ANALYSIS_SCHEMA_VERSION == 4
    assert Settings.__dataclass_fields__["analysis_schema_version"].default == 4
    assert settings.analysis_schema_version == 4
    calls = {"n": 0}

    def fake_call(_creative: RankedCreative, _asset: Path | None) -> CreativeAnalysis:
        calls["n"] += 1
        return CreativeAnalysis(
            visual_hook="v3",
            primary_promise="p",
            audience_intent="a",
            cta_intent="c",
            suggested_landing_theme="t",
            palette=["#C8F520"],
            format="ugc-candid",
        )

    image = tmp_path / "ad.jpg"
    image.write_bytes(b"one")
    creative = _ranked(imagePath=str(image))
    settings.analysis_schema_version = 3
    analyze_creative(creative, settings, call_model=fake_call)
    settings.analysis_schema_version = 4
    cached = analyze_creative(creative, settings, call_model=fake_call)
    assert calls["n"] == 2 and cached.schema_version == 4
    assert cached.analysis.format == "ugc-candid"


def test_analysis_normalises_format_and_camera_feel() -> None:
    base = {
        "visualHook": "x",
        "primaryPromise": "y",
        "audienceIntent": "z",
        "ctaIntent": "convert",
        "suggestedLandingTheme": "t",
    }
    parsed = CreativeAnalysis.model_validate(
        {**base, "format": "Before/After", "hasRealPerson": True, "cameraFeel": "handheld"}
    )
    assert parsed.format == "before-after"
    assert parsed.has_real_person is True
    assert parsed.camera_feel == "phone"
    assert CreativeAnalysis.model_validate({**base, "format": "UGC"}).format == "ugc-candid"
    assert CreativeAnalysis.model_validate({**base, "format": "hologram"}).format == "other"
    assert CreativeAnalysis.model_validate({**base, "format": ""}).format is None
    weird = CreativeAnalysis.model_validate({**base, "cameraFeel": "dreamy"})
    assert weird.camera_feel is None
    # v2 cache files and the title/body fallback carry none of the v3 fields.
    assert CreativeAnalysis.model_validate(base).format is None


def test_describe_prompt_asks_for_the_v3_fields() -> None:
    import inspect

    from funnel_growth_agent import gemini

    source = inspect.getsource(gemini._gemini_call)
    assert "format (exactly one of" in source
    assert "hasRealPerson" in source and "cameraFeel" in source


def test_fallback_analysis_has_no_format(settings) -> None:
    cached = analyze_creative(_ranked(imagePath="/no/such/file.jpg"), settings, call_model=None)
    assert cached.analysis.format is None and cached.analysis.camera_feel is None


def test_fingerprint_uses_sha_when_file_exists(tmp_path: Path) -> None:
    path = tmp_path / "a.jpg"
    path.write_bytes(b"abc")
    first = asset_fingerprint(path)
    path.write_bytes(b"abcd")
    assert first != asset_fingerprint(path)


def test_analysis_schema_accepts_newline_string_for_list_fields() -> None:
    from funnel_growth_agent.models import CreativeAnalysis

    parsed = CreativeAnalysis.model_validate(
        {
            "visualHook": "Colour swatch",
            "primaryPromise": "Your colours as vectors",
            "audienceIntent": "designers",
            "ctaIntent": "try",
            "visibleText": "Your colors.\n545516\nPOSH",
            "productClaims": "",
            "suggestedLandingTheme": "vector colours",
        }
    )
    assert parsed.visible_text == ["Your colors.", "545516", "POSH"]
    assert parsed.product_claims == []


def test_stale_gemini_cache_is_kept_when_no_model_can_rerun(settings, tmp_path: Path) -> None:
    """The demo replay analyses with call_model=False; a schema bump must not overwrite a
    real Gemini read with the title/body fallback."""

    def fake_call(_creative: RankedCreative, _asset: Path | None) -> CreativeAnalysis:
        return CreativeAnalysis(
            visual_hook="gemini saw a strawberry",
            primary_promise="p",
            audience_intent="a",
            cta_intent="c",
            suggested_landing_theme="t",
        )

    image = tmp_path / "ad.jpg"
    image.write_bytes(b"one")
    creative = _ranked(imagePath=str(image))
    settings.analysis_schema_version = 2
    analyze_creative(creative, settings, call_model=fake_call)
    settings.analysis_schema_version = 3
    kept = analyze_creative(creative, settings, call_model=False)
    assert kept.schema_version == 2 and kept.analysis.visual_hook == "gemini saw a strawberry"
    on_disk = (settings.analysis_dir / "ad_1.json").read_text(encoding="utf-8")
    assert "gemini saw a strawberry" in on_disk
    rerun = analyze_creative(creative, settings, call_model=fake_call)
    assert rerun.schema_version == 3
