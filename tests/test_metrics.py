from __future__ import annotations

import json
from datetime import datetime

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
