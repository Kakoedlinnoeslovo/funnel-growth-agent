from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from funnel_growth_agent.config import Settings
from funnel_growth_agent.metrics import (
    StaleReportError,
    get_landing_cta_metrics,
    load_latest_reports,
)


def test_metrics_use_quiz_flow_and_label_v6_as_proxy(settings: Settings) -> None:
    slice_ = get_landing_cta_metrics(settings)
    assert slice_.baseline.funnel_id == "recraft-quiz"
    assert slice_.baseline.version == "v6"
    assert slice_.baseline.is_proxy is True
    assert slice_.baseline.landing_people == 1009
    assert slice_.baseline.cta_people == 120
    assert slice_.baseline.cta_rate == pytest.approx(120 / 1009)
    assert slice_.fresh is True
    assert slice_.report_kind == "week"
    assert slice_.daily is not None
    assert slice_.daily.landing_people == 1009


def test_stale_report_tells_human_to_run_growth_loop(settings: Settings) -> None:
    settings.now = "2026-09-21T12:00:00"
    with pytest.raises(StaleReportError, match="growth-loop daily"):
        get_landing_cta_metrics(settings)


def test_low_volume_is_treated_as_not_fresh_enough(settings: Settings) -> None:
    week = settings.reports_dir / "2026-09-18_week" / "report_data.json"
    data = json.loads(week.read_text())
    data["ph_flows"][0]["landing_people"] = 20
    data["ph_flows"][0]["rows"][0]["viewed"] = 20
    week.write_text(json.dumps(data))
    with pytest.raises(StaleReportError, match="MIN_LANDING_PEOPLE|100"):
        get_landing_cta_metrics(settings)


def test_latest_weekly_is_preferred_over_daily(settings: Settings) -> None:
    weekly, daily = load_latest_reports(settings)
    assert weekly is not None
    assert weekly["period"]["kind"] == "week"
    assert daily is not None
    assert daily["period"]["kind"] == "day"


def test_metrics_never_claim_a_generated_at_from_the_future() -> None:
    assert datetime.fromisoformat("2026-09-18T11:56:17")


def _quiz_flow(funnel_id: str, version: str, landing: int, cta: int) -> dict:
    return {
        "funnel_id": funnel_id,
        "funnel_version": version,
        "landing_people": landing,
        "payments": 0,
        "rows": [
            {
                "node_id": "landing",
                "kind": "screen",
                "viewed": landing,
                "continue_rate": cta / landing,
            },
            {"node_id": "making", "kind": "screen", "viewed": cta, "continue_rate": 0.5},
        ],
    }


def _add_weekly_flow(settings: Settings, flow: dict) -> None:
    week: Path = settings.reports_dir / "2026-09-18_week" / "report_data.json"
    data = json.loads(week.read_text())
    data["ph_flows"].append(flow)
    week.write_text(json.dumps(data))


def test_base_version_flow_beats_a_busier_proxy(settings: Settings) -> None:
    # v6 has 1009 people in the fixture; the base (v7) has fewer but enough to measure.
    _add_weekly_flow(settings, _quiz_flow("recraft-quiz-v7", "v7", 300, 45))
    baseline = get_landing_cta_metrics(settings).baseline
    assert baseline.version == "v7"
    assert baseline.is_proxy is False
    assert baseline.landing_people == 300
    assert baseline.cta_people == 45


def test_thin_base_version_flow_falls_back_to_proxy(settings: Settings) -> None:
    _add_weekly_flow(settings, _quiz_flow("recraft-quiz-v7", "v7", 40, 6))
    baseline = get_landing_cta_metrics(settings).baseline
    assert baseline.version == "v6"
    assert baseline.is_proxy is True
    assert baseline.landing_people == 1009
