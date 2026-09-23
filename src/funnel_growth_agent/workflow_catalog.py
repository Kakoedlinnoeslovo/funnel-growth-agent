"""Report and baseline catalog for explicit, unranked creative selection."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .config import Settings
from .creatives import is_comparable_destination
from .landing import HERO_COMPONENTS, get_current_landing
from .metrics import baseline_from_report, load_latest_reports
from .models import Baseline, MetricsSlice, RankedCreative

VERSION = re.compile(
    r"^(?:(?!f/|v/|pricing/|v[1-9])[a-z][a-z0-9-]{0,31}/)?v[1-9][0-9]*(?:_a[1-9][0-9]*)?$"
)
METRICS = {
    "spend": "spend_usd",
    "impressions": "impressions",
    "clicks": "link_clicks",
    "ctr": "ctr",
    "cpc": "cpc",
    "leads": "platform_leads",
    "checkouts": "checkouts",
    "payments": "payments",
    "costPerLead": "cost_per_lead",
}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def safe_version(root: Path, version: str) -> Path:
    if not VERSION.fullmatch(version):
        raise ValueError("Invalid baseline version")
    path = (root / version).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Baseline must be inside the pricing lab")
    return path


def baseline_catalog(settings: Settings) -> list[dict[str, Any]]:
    root = settings.pricing_lab_dir / "funnels"
    site = YAML(typ="safe").load(settings.site_path.read_text()) or {}
    found = [*root.glob("*/funnel.yaml"), *root.glob("*/*/funnel.yaml")]
    out = []
    for manifest in sorted(found):
        version = manifest.parent.relative_to(root).as_posix()
        if not VERSION.fullmatch(version):
            continue
        folder = safe_version(root, version)
        if not (folder / "steps/landing.yaml").is_file():
            continue
        landing = get_current_landing(replace(settings, base_version=version))
        sections = landing["sections"]
        supported = bool(sections and sections[0]["component"] in HERO_COMPONENTS)
        out.append(
            {
                "version": version,
                "description": str((site.get("versions") or {}).get(version) or version),
                "default": version == site.get("default_version"),
                "published": version in (site.get("published_versions") or []),
                "supported": supported,
                "limitation": None
                if supported
                else "This page stores its content in a custom React renderer rather than editable landing sections.",
                "hash": landing["hash"],
            }
        )
    return out


def local_asset(settings: Settings, raw: Any, creative_id: str, suffix: str) -> str | None:
    candidates = []
    if raw:
        path = Path(str(raw)).expanduser()
        candidates.append(path if path.is_absolute() else settings.growth_loop_dir / path)
    if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", creative_id):
        candidates.append(
            settings.growth_loop_dir / "data/output/creative/live" / f"{creative_id}.{suffix}"
        )
    for path in candidates:
        resolved = path.resolve()
        if resolved.is_relative_to(settings.growth_loop_dir.resolve()) and resolved.is_file():
            return str(resolved)
    return None


def report_snapshot(settings: Settings) -> dict[str, Any]:
    weekly, _ = load_latest_reports(settings)
    if weekly is None:
        raise ValueError("No weekly report available. You can still upload creatives to generate a page.")
    report = {
        key: weekly.get(key)
        for key in ("generated_at", "period", "run_date", "title", "ph_flows", "creative_audience", "_path")
    }
    report["creatives"] = [
        {key: value for key, value in row.items() if not key.endswith("b64")}
        for row in weekly.get("creatives") or []
    ]
    return report


def report_warnings(report: dict[str, Any], settings: Settings) -> list[str]:
    warnings = []
    stamp = report.get("generated_at")
    try:
        generated = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).replace(tzinfo=None)
        age = (settings.clock().replace(tzinfo=None) - generated).total_seconds() / 3600
        if age > settings.max_report_age_hours:
            warnings.append(
                f"Weekly report is {age / 24:.1f} days old; performance may have changed."
            )
    except ValueError:
        warnings.append("Report generation time is unavailable.")
    return warnings


def creative_catalog(report: dict[str, Any], settings: Settings) -> list[dict[str, Any]]:
    out, seen = [], set()
    for raw in report.get("creatives") or []:
        cid = str(raw.get("ad_id") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        image = local_asset(settings, raw.get("image_path"), cid, "jpg")
        video = local_asset(settings, raw.get("video_path") or raw.get("video_local"), cid, "mp4")
        metrics = {key: raw.get(source) for key, source in METRICS.items()}
        adset_id = str(raw.get("adset_id") or "")
        adset_name = str(raw.get("adset_name") or "")
        group = adset_id or (
            "name-" + digest([raw.get("campaign_name"), adset_name])[:16] if adset_name else None
        )
        warnings = []
        sufficient = (
            (metrics["spend"] or 0) >= settings.min_creative_spend
            and (metrics["clicks"] or 0) >= settings.min_creative_clicks
            and (metrics["impressions"] or 0) >= settings.min_creative_impressions
        )
        if not sufficient:
            warnings.append("Limited traffic; directional evidence only.")
        if not image and not video:
            warnings.append("Ad asset unavailable; analysis will use ad copy only.")
        comparable = is_comparable_destination(raw.get("link_url"), raw.get("lp"))
        if not comparable:
            warnings.append(
                "Destination differs from the pricing lab; results may not be comparable."
            )
        if metrics["payments"]:
            signal = "Observed payments"
        elif metrics["checkouts"]:
            signal = "Observed checkouts"
        elif metrics["leads"]:
            signal = "Meta-reported leads"
        else:
            signal = "Traffic signal only"
        out.append(
            {
                "id": cid,
                "source": "report",
                "name": str(raw.get("ad_name") or cid),
                "adsetId": group,
                "adsetName": adset_name,
                "campaignName": str(raw.get("campaign_name") or ""),
                "title": str(raw.get("title") or ""),
                "body": str(raw.get("body") or ""),
                "linkUrl": raw.get("link_url"),
                "metrics": metrics,
                "imagePath": image,
                "videoPath": video,
                "warnings": warnings,
                "signal": signal,
                "sufficient": sufficient,
            }
        )
    return sorted(out, key=lambda item: -(item["metrics"]["spend"] or 0))


def ranked_selection(rows: list[dict[str, Any]]) -> list[RankedCreative]:
    return [
        RankedCreative(
            creative_id=row["id"],
            ad_name=row["name"],
            adset_id=row["adsetId"],
            adset_name=row["adsetName"],
            campaign_name=row["campaignName"],
            title=row["title"],
            body=row["body"],
            link_url=row["linkUrl"],
            spend=row["metrics"]["spend"],
            impressions=row["metrics"]["impressions"],
            clicks=row["metrics"]["clicks"],
            ctr=row["metrics"]["ctr"],
            cpc=row["metrics"]["cpc"],
            leads=row["metrics"]["leads"],
            checkouts=row["metrics"]["checkouts"],
            payments=row["metrics"]["payments"],
            image_path=row["imagePath"],
            video_path=row["videoPath"],
            video_duration=row.get("duration"),
            reason_selected="Explicit user selection. "
            + row["signal"]
            + "; "
            + " ".join(row["warnings"]),
        )
        for row in rows
    ]


def snapshot_metrics(report: dict[str, Any], settings: Settings) -> MetricsSlice:
    baseline = baseline_from_report(
        report, base_version=settings.base_version, min_landing_people=settings.min_landing_people
    )
    if baseline is None:
        baseline = Baseline(
            source="unavailable",
            funnel_id="unavailable",
            version=settings.base_version,
            is_proxy=True,
            landing_people=0,
            cta_people=0,
            cta_rate=0,
        )
    try:
        generated = datetime.fromisoformat(
            str(report.get("generated_at")).replace("Z", "+00:00")
        ).replace(tzinfo=None)
        age = max(0, (settings.clock().replace(tzinfo=None) - generated).total_seconds() / 3600)
    except ValueError:
        age = 0
    return MetricsSlice(
        generated_at=str(report.get("generated_at") or ""),
        age_hours=round(age, 2),
        fresh=not report_warnings(report, settings)
        and baseline.landing_people >= settings.min_landing_people,
        report_kind="week",
        report_path=str(report.get("_path") or ""),
        baseline=baseline,
    )
