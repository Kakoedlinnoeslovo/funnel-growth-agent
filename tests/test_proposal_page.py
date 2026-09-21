from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

from funnel_growth_agent.media import build_tile_spec, generate_tile, load_cached_tiles
from funnel_growth_agent.memory import append_run
from funnel_growth_agent.models import (
    Baseline,
    CreativeEvidence,
    MediaPlan,
    MemoryRow,
    SavedProposal,
    ShowcaseImagePlan,
)
from funnel_growth_agent.proposal_page import (
    composition_chips,
    copy_rows,
    render_page,
    write_page,
)
from redesign_helpers import BRIEF_TILE, FakeJudge, fake_media_tools, redesign_payload

BASELINE = Baseline(
    funnel_id="recraft-quiz",
    version="v6",
    funnel_version="v6",
    is_proxy=True,
    period="2026-09-17",
    landing_people=1009,
    cta_people=120,
    cta_rate=120 / 1009,
)


def _row(settings, run_id: str, payload: dict, *, status="proposed", variant=None) -> MemoryRow:
    saved = SavedProposal.model_validate(
        {
            "runId": run_id,
            "baseVersion": "v7",
            "baseLandingHash": "abc",
            "baseline": BASELINE.model_dump(by_alias=True),
            "creativeEvidence": [
                CreativeEvidence(
                    creative_id="ad_1",
                    reason_selected="highest lab payments",
                    primary_promise="JPG in, editable SVG out <b>",
                ).model_dump(by_alias=True)
            ],
            **payload,
        }
    )
    row = MemoryRow(
        run_id=run_id,
        created_at=datetime(2026, 9, 20, 23, 47).isoformat(timespec="seconds"),
        status=status,
        base="v7",
        variant=variant,
        proposal=saved.model_dump(by_alias=True),
    )
    append_run(settings, row)
    return row


def test_copy_rows_only_lists_fields_that_differ_from_base(settings) -> None:
    payload = redesign_payload(
        copy={
            "hero": {"headline": ["Drop a JPG.", "Get an editable SVG."], "ctaLabel": "Vectorize"},
            "finalCta": {"headline": "Ready to vectorize?"},
            "inlineCta": {"ctaLabel": "Start for free"},
        }
    )
    saved = SavedProposal.model_validate(
        {"runId": "r", "baseVersion": "v7", "baseLandingHash": "x", "baseline": BASELINE, **payload}
    )
    from funnel_growth_agent.proposal_page import base_sections

    _ids, sections = base_sections(settings, "v7")
    rows = dict(copy_rows(saved.changes, sections))
    # The hero headline equals the base, so only the label shows; inline-cta is unchanged.
    assert rows["Hero"] == [("ctaLabel", "Start Creating", "Vectorize")]
    assert rows["Final CTA"] == [("headline", "Ready to start creating?", "Ready to vectorize?")]
    assert "Inline CTA (every one on the page)" not in rows


def test_composition_chips_mark_moved_and_omitted(settings) -> None:
    from funnel_growth_agent.models import CompositionChanges
    from funnel_growth_agent.proposal_page import base_sections

    ids, _ = base_sections(settings, "v7")
    composition = CompositionChanges(
        order=[
            "press-quotes",
            "logo-strip",
            "showcase",
            "inline-cta",
            "video-cta",
            "plan-preview",
            "final-cta",
        ],
        omit=["style-switcher", "inline-cta-2"],
    )
    html = composition_chips(composition, ids)
    assert "<span class='chip moved'>press-quotes</span>" in html
    assert "<span class='chip omit'>style-switcher</span>" in html
    assert html.index("kittl-hero") < html.index("press-quotes")
    assert "<span class='chip'>final-cta</span>" in html


def test_page_shows_changes_clip_tiles_and_escapes(settings, tmp_path: Path) -> None:
    second = copy.deepcopy(BRIEF_TILE) | {"slot": 1, "references": ["previous"]}
    payload = redesign_payload(
        media={
            "heroVideo": {
                "source": "youtube",
                "videoId": "z0r74lakHOM",
                "start": 3,
                "duration": 12,
                "label": "Recraft Vectorize turning a JPG logo into editable paths",
            },
            "showcase": [copy.deepcopy(BRIEF_TILE), second],
        }
    )
    _row(settings, "run-1", payload, status="applied", variant="v7_a1")
    # Slot 0 has candidates and a judge in the cache; slot 1 was never generated.
    plan = ShowcaseImagePlan.model_validate(BRIEF_TILE)
    spec = build_tile_spec(plan, settings, "v7")
    generate_tile(
        spec, settings, fake_media_tools(judge=FakeJudge(pick=2)), lambda *_: None, variants=3
    )
    creative = settings.growth_loop_dir / "data" / "output" / "creative" / "live"
    creative.mkdir(parents=True)
    (creative / "ad_1.jpg").write_bytes(b"jpg")

    path = write_page(settings, "run-1")
    html = path.read_text(encoding="utf-8")
    assert path == settings.media_cache_dir / "pages" / "run-1.html"
    assert "applied · v7_a1" in html and "http://localhost:5173/pm/v7_a1" in html
    assert "11.9%" in html and "1,009 landing" in html and "proxy" in html
    assert "JPG in, editable SVG out &lt;b&gt;" in html  # model text is escaped
    assert (
        "class='before'>Start Creating<" in html and "class='after lit'>Vectorize my image<" in html
    )
    assert "copy-first" in html and "video-first" in html
    assert "youtube z0r74lakHOM" in html and "3s + 12s" in html
    assert "Not produced yet" in html  # no encoded clip in the cache
    assert html.count("class='cand chosen'") == 1 and "#2 <span class='badge'>chosen</span>" in html
    assert f"../tiles/{spec.stem}.cand0.png" in html
    assert "background:#C8F520" in html  # palette swatch
    assert "No candidates yet. Run funnel-growth tiles run-1" in html  # slot 1 resolves, no images
    assert "Alternative ideas (2)" in html
    assert html.count("<script") == 0


def test_page_for_no_experiment(settings) -> None:
    row = _row(
        settings,
        "run-2",
        {
            "decision": "no_experiment",
            "reason": "Landing already matches the ads.",
            "otherIdeas": ["x"],
        },
    )
    html = render_page(settings, row, base_dir=settings.media_cache_dir / "pages")
    assert "No experiment" in html and "Landing already matches the ads." in html
    assert "No files were modified" in html and "Changes" not in html


def test_load_cached_tiles_reads_without_generating(settings) -> None:
    first = ShowcaseImagePlan.model_validate(BRIEF_TILE)
    second = ShowcaseImagePlan.model_validate(BRIEF_TILE | {"slot": 1, "references": ["previous"]})
    plan = MediaPlan(showcase=[first, second])
    assert load_cached_tiles(plan, settings, "v7")[0].candidates == []
    tools = fake_media_tools()
    generate_tile(
        build_tile_spec(first, settings, "v7"), settings, tools, lambda *_: None, variants=2
    )
    results = load_cached_tiles(plan, settings, "v7")
    assert len(results) == 2  # slot 1 resolves `previous` against the cached slot-0 webp
    assert len(results[0].candidates) == 2 and results[0].judge is not None and results[0].cached
    assert results[1].candidates == [] and results[1].judge is None
    assert len(tools.images.calls) == 1  # nothing generated by the loader
