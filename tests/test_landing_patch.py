from __future__ import annotations

import pytest
from ruamel.yaml import YAML

from funnel_growth_agent.landing import resolve_section_ids
from funnel_growth_agent.landing_diff import ApplyError, ProducedFiles
from funnel_growth_agent.landing_patch import ProducedMedia, compose_order, patch_redesign
from funnel_growth_agent.models import CompositionChanges, RedesignChanges
from redesign_helpers import redesign_proposal

IDS = [
    "kittl-hero",
    "logo-strip",
    "showcase",
    "inline-cta",
    "style-switcher",
    "inline-cta-2",
    "press-quotes",
    "video-cta",
    "plan-preview",
    "final-cta",
]


def test_compose_order_omit_only_keeps_base_order() -> None:
    result = compose_order(IDS, CompositionChanges(omit=["style-switcher"]))
    assert result == [sid for sid in IDS if sid != "style-switcher"]


def test_compose_order_full_order_after_hero() -> None:
    order = [
        "press-quotes",
        "logo-strip",
        "showcase",
        "inline-cta",
        "video-cta",
        "plan-preview",
        "final-cta",
    ]
    composition = CompositionChanges(order=order, omit=["style-switcher", "inline-cta-2"])
    assert compose_order(IDS, composition) == ["kittl-hero"] + order


def test_compose_order_rejects_incomplete_or_unknown_ids() -> None:
    with pytest.raises(ApplyError, match="unknown section id 'nope'"):
        compose_order(IDS, CompositionChanges(omit=["nope"]))
    with pytest.raises(ApplyError, match="missing"):
        compose_order(IDS, CompositionChanges(order=["logo-strip", "final-cta"]))


def test_patch_redesign_writes_copy_layout_order_and_media(settings) -> None:
    landing = settings.landing_path
    proposal = redesign_proposal()
    assert isinstance(proposal.changes, RedesignChanges)
    stem = "assets/video/hero-youtube-z0r74lakhom-30-120"
    media = ProducedMedia(
        hero_video_ref={
            "label": "Recraft Vectorize turning a JPG logo into editable paths",
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
        },
        showcase={("Vectors", 0): "assets/showcase/gen-abc.webp"},
        files=ProducedFiles(),
    )
    order = patch_redesign(landing, proposal.changes, media)
    assert order[:2] == ["kittl-hero", "press-quotes"]
    text = landing.read_text()
    assert "    - component: kittl-hero\n" in text
    doc = YAML(typ="safe").load(text)
    sections = doc["props"]["sections"]
    assert resolve_section_ids(sections) == order
    hero = sections[0]
    assert hero["layout"] == "video-first"
    assert hero["stickyCta"] is True
    assert hero["ctaLabel"] == "Vectorize my image"
    assert list(hero["video"].keys()) == ["label", "desktop", "phone"]
    assert list(hero["video"]["desktop"].keys()) == ["webm", "mp4", "poster"]
    inline = [s for s in sections if s["component"] == "inline-cta"]
    assert len(inline) == 1 and inline[0]["ctaLabel"] == "Vectorize my image"
    final = sections[-1]
    assert final["headline"] == "Ready to vectorize?"
    showcase = next(s for s in sections if s["component"] == "showcase")
    assert showcase["groups"][0]["images"][0] == "assets/showcase/gen-abc.webp"
    assert showcase["groups"][0]["images"][1] == "assets/showcase/vector-2.webp"
    assert doc["props"]["pageTitle"] == "Recraft"


def test_inline_copy_hits_every_inline_cta_without_composition(settings) -> None:
    proposal = redesign_proposal(composition=None, media=None, layout=None)
    patch_redesign(settings.landing_path, proposal.changes)
    doc = YAML(typ="safe").load(settings.landing_path.read_text())
    inline = [s for s in doc["props"]["sections"] if s["component"] == "inline-cta"]
    assert [s["ctaLabel"] for s in inline] == ["Vectorize my image", "Vectorize my image"]
    assert [s["headline"] for s in inline] == ["This is what you get back.", "Your own style."]


def test_patch_redesign_rejects_unknown_showcase_group(settings) -> None:
    proposal = redesign_proposal(composition=None, media=None)
    media = ProducedMedia(showcase={("Nope", 0): "assets/showcase/gen-abc.webp"})
    with pytest.raises(ApplyError, match="showcase group 'Nope' not found"):
        patch_redesign(settings.landing_path, proposal.changes, media)
