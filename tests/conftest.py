from __future__ import annotations

import json
from pathlib import Path

import pytest

from funnel_growth_agent.config import Settings


def _flow(funnel_id: str, version: str, landing: int, cta: int) -> dict:
    rate = cta / landing if landing else None
    return {
        "funnel_id": funnel_id,
        "funnel_version": version,
        "landing_people": landing,
        "payments": 0,
        "rows": [
            {"node_id": "landing", "kind": "screen", "viewed": landing, "continue_rate": rate},
            {"node_id": "making", "kind": "screen", "viewed": cta, "continue_rate": 0.5},
        ],
    }


def _creative(**overrides: object) -> dict:
    row = {
        "ad_id": "ad_1",
        "ad_name": "jpg_to_svg_01",
        "title": "Convert JPG to SVG",
        "body": "Turn any image into an editable vector.",
        "link_url": "https://recraft-meta.vercel.app/?utm_source=facebook",
        "lp": "vercel",
        "spend_usd": 80.0,
        "impressions": 4000,
        "link_clicks": 80,
        "ctr": 0.02,
        "cpc": 1.0,
        "checkouts": 2,
        "payments": 1,
        "image_path": None,
        "video_path": None,
        "thumb_b64": "SHOULD_NOT_REACH_CLAUDE",
        "full_b64": "SHOULD_NOT_REACH_CLAUDE",
    }
    row.update(overrides)
    return row


def sample_report(**overrides: object) -> dict:
    report = {
        "run_date": "2026-09-18",
        "generated_at": "2026-09-18T11:56:17",
        "title": "Weekly — 2026-09-11 → 2026-09-17",
        "period": {
            "kind": "week",
            "current": {"start": "2026-09-11", "end": "2026-09-17"},
        },
        "ph_flows": [
            _flow("recraft-quiz", "v6", 1009, 120),
            _flow("kittl", "v1", 200, 20),
        ],
        "creatives": [
            _creative(),
            _creative(
                ad_id="ad_go",
                ad_name="go_link",
                link_url="https://recraft.ai/go/ads",
                spend_usd=500.0,
                link_clicks=200,
                payments=9,
            ),
            _creative(
                ad_id="ad_old_window_should_not_matter",
                ad_name="other_product",
                link_url="https://example.com/pricing",
                lp="main",
                spend_usd=300.0,
                link_clicks=150,
                payments=4,
            ),
            _creative(
                ad_id="ad_tiny",
                ad_name="tiny",
                spend_usd=0.4,
                link_clicks=1,
                impressions=10,
            ),
            _creative(
                ad_id="ad_2",
                ad_name="vector_02",
                title="Editable vectors",
                spend_usd=40.0,
                link_clicks=40,
                checkouts=1,
                payments=0,
            ),
            _creative(
                ad_id="ad_3",
                ad_name="vector_03",
                title="Drop a JPG",
                spend_usd=20.0,
                link_clicks=25,
                checkouts=0,
                payments=0,
            ),
        ],
    }
    report.update(overrides)
    return report


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    reports = tmp_path / "growth-loop" / "data" / "output" / "reports"
    week = reports / "2026-09-18_week"
    week.mkdir(parents=True)
    (week / "report_data.json").write_text(json.dumps(sample_report()), encoding="utf-8")
    daily = reports / "2026-09-18"
    daily.mkdir()
    daily_report = sample_report(
        title="Daily — 2026-09-17",
        period={"kind": "day", "current": {"start": "2026-09-17", "end": "2026-09-17"}},
        generated_at="2026-09-18T11:13:34",
    )
    (daily / "report_data.json").write_text(json.dumps(daily_report), encoding="utf-8")

    lab = tmp_path / "recraft-pricing-lab"
    landing = lab / "funnels" / "v7" / "steps"
    landing.mkdir(parents=True)
    (lab / "funnels" / "v7" / "funnel.yaml").write_text(
        "id: recraft-quiz-v7\ntitle: Onboarding quiz v7\nlandingUser: pricing-lab-v7\nsteps:\n  - landing\n",
        encoding="utf-8",
    )
    (landing / "landing.yaml").write_text(
        (
            "id: landing\ncomponent: landing\npath: /\ntitle: Landing\nprops:\n"
            "  pageTitle: Recraft\n  sections:\n"
            "    - component: kittl-hero\n      layout: video-first\n      stickyCta: true\n"
            "      headline: [Drop a JPG., Get an editable SVG.]\n"
            "      subhead: Upload a logo.\n      ctaLabel: Start Creating\n"
            "      reassurance: Free to use.\n"
            "    - component: logo-strip\n      headline: Loved by designers\n"
            "    - component: video-cta\n      headline: [Try in Recraft Studio]\n"
            "      subhead: Studio copy\n      ctaLabel: Try it free\n"
            "    - component: final-cta\n      headline: Ready to start creating?\n"
            "      subhead: It takes seconds.\n      ctaLabel: Start Creating\n"
        ),
        encoding="utf-8",
    )
    (lab / "funnels" / "site.yaml").write_text(
        "default_version: v7\npublished_versions:\n  - v7\nversions:\n  v7: Onboarding quiz\n",
        encoding="utf-8",
    )
    memory = tmp_path / "data" / "memory"
    analysis = tmp_path / "data" / "creative_analysis"
    memory.mkdir(parents=True)
    analysis.mkdir(parents=True)
    return Settings(
        growth_loop_dir=tmp_path / "growth-loop",
        pricing_lab_dir=lab,
        data_dir=tmp_path / "data",
        base_version="v7",
        max_report_age_hours=48,
        min_landing_people=100,
        min_creative_spend=5.0,
        min_creative_clicks=10,
        now="2026-09-18T18:00:00",
    )
