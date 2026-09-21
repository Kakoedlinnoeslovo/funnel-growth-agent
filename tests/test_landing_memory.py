from __future__ import annotations

import hashlib

from funnel_growth_agent.landing import get_current_landing, landing_hash
from funnel_growth_agent.memory import append_run, get_previous_runs
from funnel_growth_agent.models import MemoryRow


def test_current_landing_is_structured_and_omits_media_bytes(settings) -> None:
    landing = get_current_landing(settings)
    assert landing["version"] == "v7"
    assert landing["hash"] == landing_hash(settings.landing_path)
    components = [section["component"] for section in landing["sections"]]
    assert components[0] == "kittl-hero"
    assert "Start Creating" in landing["sections"][0]["ctaLabel"]
    blob = str(landing)
    assert "base64" not in blob
    assert "webm" not in blob


def test_current_landing_exposes_section_ids_and_group_labels(settings) -> None:
    landing = get_current_landing(settings)
    ids = [section["id"] for section in landing["sections"]]
    assert ids == [
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
    showcase = landing["sections"][2]
    assert showcase["groups"] == [
        {"label": "Vectors", "imageCount": 3},
        {"label": "Photos", "imageCount": 3},
    ]
    assert landing["mobile"] == "phone-first"
    assert "assets/" not in str(landing)


def test_previous_runs_always_include_evaluation_and_learning(settings) -> None:
    append_run(
        settings,
        MemoryRow(
            run_id="2026-09-18T1640-v7-001",
            created_at="2026-09-18T16:40:00",
            status="proposed",
            base="v7",
            variant=None,
            proposal={
                "experimentType": "hero_copy",
                "hypothesis": "Match the hero to JPG→SVG ads.",
                "changes": {"ctaLabel": "Convert my image"},
            },
            deployed_at=None,
            evaluation=None,
            learning=None,
        ),
    )
    runs = get_previous_runs(settings, experiment_type="hero_copy", limit=5)
    assert len(runs) == 1
    assert runs[0].evaluation is None
    assert runs[0].learning is None
    assert runs[0].hypothesis.startswith("Match")
    dumped = runs[0].model_dump()
    assert "evaluation" in dumped
    assert "learning" in dumped


def test_previous_runs_filter_by_type_and_limit(settings) -> None:
    for index in range(6):
        append_run(
            settings,
            MemoryRow(
                run_id=f"run-{index}",
                created_at=f"2026-09-18T16:4{index}:00",
                status="proposed",
                base="v7",
                variant=None,
                proposal={"experimentType": "hero_copy" if index < 5 else "composition"},
                deployed_at=None,
                evaluation=None,
                learning=None,
            ),
        )
    runs = get_previous_runs(settings, experiment_type="hero_copy", limit=5)
    assert len(runs) == 5
    assert all((run.experiment_type == "hero_copy") for run in runs)


def test_landing_hash_is_sha256_of_file_bytes(settings) -> None:
    raw = settings.landing_path.read_bytes()
    assert landing_hash(settings.landing_path) == hashlib.sha256(raw).hexdigest()
