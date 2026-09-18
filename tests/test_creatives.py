from __future__ import annotations

from funnel_growth_agent.creatives import get_top_creatives
from funnel_growth_agent.metrics import load_latest_reports


def test_top_creatives_keep_only_comparable_lab_traffic(settings) -> None:
    weekly, _ = load_latest_reports(settings)
    ranked = get_top_creatives(weekly, settings)
    ids = [row.creative_id for row in ranked]
    assert "ad_go" not in ids
    assert "ad_old_window_should_not_matter" not in ids
    assert "ad_tiny" not in ids
    assert ids[0] == "ad_1"
    assert len(ranked) <= 3


def test_ranked_creatives_omit_base64_and_never_invent_cta(settings) -> None:
    weekly, _ = load_latest_reports(settings)
    ranked = get_top_creatives(weekly, settings)
    payload = [row.model_dump(by_alias=True) for row in ranked]
    for row in payload:
        assert "thumb_b64" not in row
        assert "full_b64" not in row
        assert "landingPeople" not in row
        assert "ctaPeople" not in row
        assert "ctaRate" not in row


def test_no_comparable_creatives_returns_empty(settings) -> None:
    weekly, _ = load_latest_reports(settings)
    for row in weekly["creatives"]:
        row["link_url"] = "https://recraft.ai/go/ads"
        row["lp"] = "main"
    assert get_top_creatives(weekly, settings) == []
