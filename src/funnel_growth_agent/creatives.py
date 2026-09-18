"""Rank comparable-traffic creatives. Software picks; Gemini only describes."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .config import Settings
from .models import RankedCreative

LAB_HOSTS = {
    "recraft-meta.vercel.app",
    "localhost",
    "127.0.0.1",
}


def is_comparable_destination(link_url: str | None, lp: str | None) -> bool:
    if not link_url:
        return False
    parsed = urlparse(link_url)
    path = parsed.path or "/"
    if "/go/" in path or path.startswith("/go"):
        return False
    host = (parsed.hostname or "").lower()
    if host in LAB_HOSTS or host.endswith(".vercel.app"):
        return lp in {None, "", "vercel"} or lp == "vercel"
    return False


def _reason(row: dict[str, Any], rank: int) -> str:
    if (row.get("payments") or 0) > 0:
        return "highest lab payments among comparable ads"
    if (row.get("checkouts") or 0) > 0:
        return "highest lab checkouts among comparable ads"
    if rank == 0:
        return "highest qualified spend and clicks among comparable ads"
    return "next strongest comparable-traffic creative"


def _rank_key(row: dict[str, Any]) -> tuple:
    spend = float(row.get("spend_usd") or 0)
    clicks = int(row.get("link_clicks") or 0)
    ctr = float(row.get("ctr") or 0)
    cpc = row.get("cpc")
    cpc_score = -float(cpc) if cpc not in (None, 0) else float("-inf")
    return (
        int(row.get("payments") or 0),
        int(row.get("checkouts") or 0),
        spend,
        clicks,
        ctr,
        cpc_score,
    )


def get_top_creatives(
    report: dict[str, Any] | None,
    settings: Settings,
    *,
    limit: int = 3,
) -> list[RankedCreative]:
    if not report:
        return []
    eligible: list[dict[str, Any]] = []
    for raw in report.get("creatives") or []:
        if not is_comparable_destination(raw.get("link_url"), raw.get("lp")):
            continue
        spend = float(raw.get("spend_usd") or 0)
        clicks = int(raw.get("link_clicks") or 0)
        impressions = raw.get("impressions")
        if spend < settings.min_creative_spend or clicks < settings.min_creative_clicks:
            continue
        if impressions is not None and int(impressions) < settings.min_creative_impressions:
            continue
        eligible.append(raw)
    eligible.sort(key=_rank_key, reverse=True)
    ranked: list[RankedCreative] = []
    for index, raw in enumerate(eligible[:limit]):
        ranked.append(
            RankedCreative(
                creative_id=str(raw.get("ad_id") or raw.get("ad_name") or f"ad_{index}"),
                ad_name=str(raw.get("ad_name") or ""),
                title=str(raw.get("title") or ""),
                body=str(raw.get("body") or ""),
                link_url=raw.get("link_url"),
                spend=float(raw.get("spend_usd") or 0),
                impressions=int(raw.get("impressions") or 0),
                clicks=int(raw.get("link_clicks") or 0),
                ctr=raw.get("ctr"),
                cpc=raw.get("cpc"),
                checkouts=int(raw.get("checkouts") or 0),
                payments=int(raw.get("payments") or 0),
                image_path=raw.get("image_path"),
                video_path=raw.get("video_path") or raw.get("video_local"),
                reason_selected=_reason(raw, index),
            )
        )
    return ranked
