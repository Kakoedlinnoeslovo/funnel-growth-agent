from __future__ import annotations

import copy
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from conftest import FAKE_LANDING
from funnel_growth_agent.landing_diff import (
    ApplyError,
    ProducedFiles,
    check_landing_diff,
    hard_diff_variant,
)

_yaml = YAML(typ="safe")


def _doc() -> dict:
    return _yaml.load(FAKE_LANDING)


def _sections(doc: dict) -> list[dict]:
    return doc["props"]["sections"]


def _by_component(doc: dict, component: str, nth: int = 0) -> dict:
    matches = [s for s in _sections(doc) if s["component"] == component]
    return matches[nth]


VIDEO_FILES = frozenset(
    {
        "assets/video/hero-youtube-abc-30-120-1280.mp4",
        "assets/video/hero-youtube-abc-30-120-1280.webm",
        "assets/video/hero-youtube-abc-30-120-poster-1280.webp",
        "assets/video/hero-youtube-abc-30-120-720.mp4",
        "assets/video/hero-youtube-abc-30-120-720.webm",
        "assets/video/hero-youtube-abc-30-120-poster-720.webp",
    }
)


def _video_ref() -> dict:
    stem = "assets/video/hero-youtube-abc-30-120"
    return {
        "label": "Recraft Vectorize turning a JPG into paths",
        "desktop": {
            "webm": f"{stem}-1280.webm",
            "mp4": f"{stem}-1280.mp4",
            "poster": f"{stem}-poster-1280.webp",
        },
        "phone": {
            "webm": f"{stem}-720.webm",
            "mp4": f"{stem}-720.mp4",
            "poster": f"{stem}-poster-720.webp",
        },
    }


def test_text_changes_on_allowed_sections_pass() -> None:
    old, new = _doc(), _doc()
    _by_component(new, "kittl-hero")["ctaLabel"] = "Vectorize my image"
    _by_component(new, "inline-cta", 1)["ctaLabel"] = "Vectorize my image"
    _by_component(new, "final-cta")["headline"] = "Ready?"
    summary = check_landing_diff(old, new)
    assert any(line.startswith("kittl-hero: ctaLabel") for line in summary)
    assert any(line.startswith("inline-cta-2: ctaLabel") for line in summary)


def test_headline_line_edits_and_line_count_changes_pass() -> None:
    old, new = _doc(), _doc()
    _by_component(new, "video-cta")["headline"] = ["See vectorize in action."]  # same length
    _by_component(new, "kittl-hero")["headline"] = ["One", "Two", "Three"]  # longer list
    summary = check_landing_diff(old, new)
    assert "video-cta: headline[0]" in summary
    assert "kittl-hero: headline" in summary
    _by_component(new, "video-cta")["video"] = {"label": "x"}
    with pytest.raises(ApplyError, match="video-cta changed \\['video'\\]"):
        check_landing_diff(old, new)


def test_reorder_of_movable_sections_passes_and_is_summarised() -> None:
    old, new = _doc(), _doc()
    sections = _sections(new)
    press = sections.pop(6)
    sections.insert(1, press)
    summary = check_landing_diff(old, new)
    assert summary[0].startswith("sections reordered: kittl-hero, press-quotes, logo-strip")


def test_hero_not_first_is_rejected() -> None:
    old, new = _doc(), _doc()
    sections = _sections(new)
    sections.append(sections.pop(0))
    with pytest.raises(ApplyError, match="kittl-hero must stay the first"):
        check_landing_diff(old, new)


def test_final_cta_must_stay_last() -> None:
    old, new = _doc(), _doc()
    sections = _sections(new)
    final = sections.pop()
    sections.insert(1, final)
    with pytest.raises(ApplyError, match="final-cta must stay the last"):
        check_landing_diff(old, new)


def test_omitting_press_quotes_is_rejected() -> None:
    old, new = _doc(), _doc()
    _sections(new).pop(6)
    with pytest.raises(ApplyError, match="press-quotes cannot be omitted"):
        check_landing_diff(old, new)


def test_omitting_three_sections_is_rejected() -> None:
    old, new = _doc(), _doc()
    sections = _sections(new)
    for index in (5, 4, 1):
        sections.pop(index)
    with pytest.raises(ApplyError, match="at most 2"):
        check_landing_diff(old, new)


def test_two_omissions_pass() -> None:
    old, new = _doc(), _doc()
    sections = _sections(new)
    for index in (5, 4):
        sections.pop(index)
    summary = check_landing_diff(old, new)
    assert "sections omitted: style-switcher, inline-cta-2" in summary


def test_invented_section_is_rejected() -> None:
    old, new = _doc(), _doc()
    _sections(new).insert(1, {"component": "quote", "text": "hi"})
    with pytest.raises(ApplyError, match="invents sections"):
        check_landing_diff(old, new)


def test_showcase_caption_change_is_rejected() -> None:
    old, new = _doc(), _doc()
    _by_component(new, "showcase")["groups"][0]["caption"] = "New caption"
    with pytest.raises(ApplyError, match="showcase may only swap images"):
        check_landing_diff(old, new)


def test_showcase_image_swap_requires_produced_file() -> None:
    old, new = _doc(), _doc()
    _by_component(new, "showcase")["groups"][0]["images"][0] = "assets/showcase/gen-abc.webp"
    with pytest.raises(ApplyError, match="was not produced"):
        check_landing_diff(old, new)
    produced = ProducedFiles(showcase=frozenset({"assets/showcase/gen-abc.webp"}))
    summary = check_landing_diff(old, new, produced)
    assert "showcase: groups[0].images[0]" in summary


def test_hero_video_must_match_produced_set() -> None:
    old, new = _doc(), _doc()
    _by_component(new, "kittl-hero")["video"] = _video_ref()
    with pytest.raises(ApplyError, match="without produced files"):
        check_landing_diff(old, new)
    partial = ProducedFiles(hero_video=frozenset(list(VIDEO_FILES)[:5]))
    with pytest.raises(ApplyError, match="was not produced"):
        check_landing_diff(old, new, partial)
    assert check_landing_diff(old, new, ProducedFiles(hero_video=VIDEO_FILES))


def test_hero_video_desktop_must_use_1280_rendition() -> None:
    old, new = _doc(), _doc()
    ref = _video_ref()
    ref["desktop"], ref["phone"] = ref["phone"], ref["desktop"]
    _by_component(new, "kittl-hero")["video"] = ref
    with pytest.raises(ApplyError, match="1280 rendition"):
        check_landing_diff(old, new, ProducedFiles(hero_video=VIDEO_FILES))


def test_video_first_without_clip_is_rejected_when_layout_changes() -> None:
    old, new = _doc(), _doc()
    _by_component(new, "kittl-hero")["layout"] = "video-first"
    with pytest.raises(ApplyError, match="video-first needs a hero video"):
        check_landing_diff(old, new)


def test_orphan_produced_file_is_rejected() -> None:
    old, new = _doc(), _doc()
    _by_component(new, "kittl-hero")["ctaLabel"] = "Go"
    with pytest.raises(ApplyError, match="not referenced"):
        check_landing_diff(
            old, new, ProducedFiles(showcase=frozenset({"assets/showcase/gen-x.webp"}))
        )


def test_thumb_rides_along_only_with_a_referenced_sibling() -> None:
    from funnel_growth_agent.landing_diff import NEW_FILE_RE

    assert NEW_FILE_RE.match("assets/thumbs/showcase/gen-abc.webp")
    assert not NEW_FILE_RE.match("assets/thumbs/gen-abc.webp")
    old, new = _doc(), _doc()
    _by_component(new, "showcase")["groups"][0]["images"][0] = "assets/showcase/gen-abc.webp"
    produced = ProducedFiles(
        showcase=frozenset({"assets/showcase/gen-abc.webp"}),
        showcase_thumbs=frozenset({"assets/thumbs/showcase/gen-abc.webp"}),
    )
    assert check_landing_diff(old, new, produced)
    orphan_thumb = ProducedFiles(
        showcase=frozenset({"assets/showcase/gen-abc.webp"}),
        showcase_thumbs=frozenset({"assets/thumbs/showcase/gen-zzz.webp"}),
    )
    with pytest.raises(ApplyError, match="no produced showcase sibling"):
        check_landing_diff(old, new, orphan_thumb)
    unreferenced = ProducedFiles(
        showcase=frozenset({"assets/showcase/gen-abc.webp", "assets/showcase/gen-zzz.webp"}),
        showcase_thumbs=frozenset({"assets/thumbs/showcase/gen-zzz.webp"}),
    )
    with pytest.raises(ApplyError, match="not referenced"):
        check_landing_diff(old, new, unreferenced)


def test_change_outside_sections_is_rejected() -> None:
    old, new = _doc(), _doc()
    new["props"]["pageTitle"] = "Other"
    with pytest.raises(ApplyError, match="outside sections"):
        check_landing_diff(old, new)


def test_locked_section_cannot_change() -> None:
    old, new = _doc(), _doc()
    _by_component(new, "plan-preview")["headline"] = "Cheaper plans"
    with pytest.raises(ApplyError, match="plan-preview \\(plan-preview\\) cannot change"):
        check_landing_diff(old, new)


def _variant(settings, tmp_path: Path) -> tuple[Path, Path]:
    import shutil

    base = settings.pricing_lab_dir / "funnels" / "v7"
    variant = tmp_path / "v7_a1"
    shutil.copytree(base, variant)
    return base, variant


def test_hard_diff_rejects_new_file_outside_media_dirs(settings, tmp_path: Path) -> None:
    base, variant = _variant(settings, tmp_path)
    (variant / "assets" / "extra.webp").write_bytes(b"x")
    with pytest.raises(ApplyError, match="file set differs"):
        hard_diff_variant(base, variant)
    produced = ProducedFiles(showcase=frozenset({"assets/extra.webp"}))
    with pytest.raises(ApplyError, match="outside assets/video"):
        hard_diff_variant(base, variant, produced)


def test_hard_diff_rejects_removed_base_file(settings, tmp_path: Path) -> None:
    base, variant = _variant(settings, tmp_path)
    (variant / "assets" / "showcase" / "photo-1.webp").unlink()
    with pytest.raises(ApplyError, match="removed base files"):
        hard_diff_variant(base, variant)


def test_hard_diff_rejects_oversized_and_empty_new_files(settings, tmp_path: Path) -> None:
    base, variant = _variant(settings, tmp_path)
    (variant / "assets" / "showcase" / "gen-abc.webp").write_bytes(b"")
    produced = ProducedFiles(showcase=frozenset({"assets/showcase/gen-abc.webp"}))
    with pytest.raises(ApplyError, match="0 bytes"):
        hard_diff_variant(base, variant, produced)


def test_hard_diff_rejects_modified_base_asset(settings, tmp_path: Path) -> None:
    base, variant = _variant(settings, tmp_path)
    (variant / "assets" / "showcase" / "photo-1.webp").write_bytes(b"changed")
    with pytest.raises(ApplyError, match="unexpected change in assets/showcase/photo-1.webp"):
        hard_diff_variant(base, variant)


def test_hard_diff_summary_for_identity_only(settings, tmp_path: Path) -> None:
    base, variant = _variant(settings, tmp_path)
    doc = copy.deepcopy(_yaml.load((variant / "funnel.yaml").read_text()))
    doc["title"] = "Landing redesign experiment v7_a1"
    YAML().dump(doc, (variant / "funnel.yaml").open("w"))
    assert hard_diff_variant(base, variant) == []
